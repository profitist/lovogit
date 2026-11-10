import asyncio
import re
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from lovogit.app.providers.pypi_dto import (
    PackageFileDTO,
    PackageIndexDTO,
    PackageInfoDTO,
)
from lovogit.app.providers.pypi_errors import (
    InvalidPyPiResponse,
    PackageNotFound,
    PyPiRateLimited,
    PyPiUnavailable,
)
from lovogit.app.providers.pypi_schemas import (
    PackageIndexApiSchema,
    PackageResponseApiSchema,
)
from lovogit.cfg.pypi import PyPiSettings


class PyPiProvider:
    """Read public package metadata and distribution indexes without downloading code."""

    def __init__(self, client: httpx.AsyncClient, settings: PyPiSettings):
        self._client = client
        self._settings = settings
        self._origin = str(settings.API_URL).rstrip("/")

    async def get_package(self, package: str) -> PackageIndexDTO:
        """Return all advertised versions and files, including pre-releases and yanks.

        Version order is unspecified. File URLs are metadata, not fetched resources.
        """
        name = _normalize_name(package)
        path = f"/simple/{name}/"
        data = await self._read(
            name,
            None,
            path,
            "application/vnd.pypi.simple.v1+json",
            PackageIndexApiSchema,
        )
        return PackageIndexDTO(
            name=data.name,
            versions=tuple(data.versions),
            files=tuple(
                PackageFileDTO(
                    filename=item.filename,
                    url=str(httpx.URL(self._origin + path).join(item.url)),
                    hashes=item.hashes,
                    requires_python=item.requires_python,
                    yanked=isinstance(item.yanked, str) or item.yanked,
                    yanked_reason=item.yanked if isinstance(item.yanked, str) else None,
                    size=item.size,
                    uploaded_at=item.uploaded_at,
                )
                for item in data.files
            ),
        )

    async def get_package_info(
        self, package: str, version: str | None = None
    ) -> PackageInfoDTO:
        """Read PyPI's default release metadata, or an explicitly requested version.

        Description is publisher-supplied package text, not necessarily release notes.
        Links are returned as published and are not visited.
        """
        name = _normalize_name(package)
        if version is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version
        ):
            raise ValueError("PyPI version must be a nonempty version string")
        suffix = f"/{quote(version, safe='')}" if version is not None else ""
        data = await self._read(
            name,
            version,
            f"/pypi/{name}{suffix}/json",
            "application/json",
            PackageResponseApiSchema,
        )
        info = data.info
        return PackageInfoDTO(
            name=info.name,
            version=info.version,
            summary=info.summary,
            description=info.description,
            description_content_type=info.description_content_type,
            requires_python=info.requires_python,
            requires_dist=tuple(info.requires_dist or ()),
            dynamic=tuple(info.dynamic or ()),
            project_urls=dict(info.project_urls or {}),
            home_page=info.home_page,
            package_url=info.package_url,
            release_url=info.release_url,
            yanked=info.yanked,
            yanked_reason=info.yanked_reason,
        )

    async def _read[T: BaseModel](
        self,
        package: str,
        version: str | None,
        path: str,
        accept: str,
        schema: type[T],
    ) -> T:
        timeout = httpx.Timeout(
            self._settings.PYPI_READ_TIMEOUT,
            connect=self._settings.PYPI_CONNECT_TIMEOUT,
        )
        request = httpx.Request(
            "GET",
            self._origin + path,
            headers={"Accept": accept, "User-Agent": "LovoGit"},
            extensions={"timeout": timeout.as_dict()},
        )
        try:
            async with asyncio.timeout(self._settings.PYPI_DEADLINE):
                response = await self._client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                try:
                    code = response.status_code
                    if code == 404:
                        raise PackageNotFound(
                            package, version, "not found", status_code=code
                        )
                    if code == 429:
                        raise PyPiRateLimited(
                            package,
                            version,
                            "rate limited",
                            status_code=code,
                            retry_after=response.headers.get("retry-after"),
                        )
                    if code >= 500:
                        raise PyPiUnavailable(
                            package, version, "server error", status_code=code
                        )
                    if code != 200:
                        raise InvalidPyPiResponse(
                            package, version, "unexpected HTTP status", status_code=code
                        )
                    content_type = (
                        response.headers.get("content-type", "").split(";")[0].strip()
                    )
                    if content_type != accept:
                        raise InvalidPyPiResponse(
                            package, version, "unexpected content type"
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        body.extend(chunk)
                        if len(body) > self._settings.PYPI_MAX_RESPONSE_BYTES:
                            raise InvalidPyPiResponse(
                                package, version, "response size limit exceeded"
                            )
                    parsed = schema.model_validate_json(body)
                    response_name = (
                        parsed.info.name
                        if isinstance(parsed, PackageResponseApiSchema)
                        else parsed.name
                    )
                    if _normalize_name(response_name) != package:
                        raise InvalidPyPiResponse(
                            package, version, "package name mismatch"
                        )
                    return parsed
                finally:
                    await response.aclose()
        except httpx.HTTPError, TimeoutError:
            raise PyPiUnavailable(
                package, version, "transport failure or deadline exceeded"
            ) from None
        except ValidationError, ValueError:
            raise InvalidPyPiResponse(
                package, version, "invalid response schema"
            ) from None


def _normalize_name(package: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", package):
        raise ValueError(
            "PyPI package name must contain ASCII letters, digits, dots, hyphens or underscores"
        )
    return re.sub(r"[-_.]+", "-", package).lower()
