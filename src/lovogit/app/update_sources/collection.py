import re
import unicodedata
from urllib.parse import urlsplit

from lovogit.app.providers.pypi import PyPiProvider
from lovogit.app.providers.pypi_dto import PackageInfoDTO
from lovogit.app.update_sources.dto import (
    LinkCategory,
    ReleaseSourcesDTO,
    RequirementDifferenceDTO,
    SourceLinkDTO,
    SourceOriginDTO,
    UpdateSourceRequest,
    UpdateSourcesDTO,
)

_LINK_CATEGORIES: dict[str, LinkCategory] = {
    "changelog": "changelog",
    "changes": "changelog",
    "history": "changelog",
    "releasenotes": "release_notes",
    "source": "source",
    "sourcecode": "source",
    "repository": "source",
    "code": "source",
    "documentation": "documentation",
    "docs": "documentation",
    "homepage": "homepage",
    "home": "homepage",
}


async def collect_sources(
    request: UpdateSourceRequest, provider: PyPiProvider
) -> UpdateSourcesDTO:
    for name in ("package", "current_version", "target_version"):
        value = getattr(request, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Cannot collect update sources: {name} must be nonempty")
    current = await provider.get_package_info(request.package, request.current_version)
    target = (
        current
        if request.current_version == request.target_version
        else await provider.get_package_info(request.package, request.target_version)
    )
    return UpdateSourcesDTO(
        request=request,
        current=_release_sources(
            current,
            SourceOriginDTO(
                "current", current.name, current.version, request.current_version
            ),
        ),
        target=_release_sources(
            target,
            SourceOriginDTO(
                "target", target.name, target.version, request.target_version
            ),
        ),
        requirements_diff=RequirementDifferenceDTO(
            current_requires_python=current.requires_python,
            target_requires_python=target.requires_python,
            added_requires_dist=tuple(
                sorted(set(target.requires_dist) - set(current.requires_dist))
            ),
            removed_requires_dist=tuple(
                sorted(set(current.requires_dist) - set(target.requires_dist))
            ),
        ),
    )


def _release_sources(
    metadata: PackageInfoDTO, origin: SourceOriginDTO
) -> ReleaseSourcesDTO:
    links = [
        SourceLinkDTO(
            origin=origin,
            field="project_urls",
            label=label,
            url=url,
            category=_LINK_CATEGORIES.get(re.sub("[ _-]", "", label.lower()), "other"),
            exclusion_reason=_url_exclusion_reason(url),
        )
        for label, url in metadata.project_urls.items()
    ]
    if metadata.home_page:
        links.append(
            SourceLinkDTO(
                origin,
                "home_page",
                "home_page",
                metadata.home_page,
                "homepage",
                _url_exclusion_reason(metadata.home_page),
            )
        )
    return ReleaseSourcesDTO(
        origin=origin,
        metadata=metadata,
        description_available=bool(
            metadata.description and metadata.description.strip()
        ),
        links=tuple(links),
    )


def _url_exclusion_reason(url: str) -> str | None:
    if not url:
        return "empty_url"
    if "\\" in url or any(
        char.isspace() or unicodedata.category(char) == "Cc" for char in url
    ):
        return "invalid_url"
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return "unsupported_scheme" if parsed.scheme else "invalid_url"
        if not parsed.hostname:
            return "invalid_url"
        if parsed.username is not None or parsed.password is not None:
            return "credentials_not_allowed"
        _ = parsed.port
    except ValueError:
        return "invalid_url"
    return None
