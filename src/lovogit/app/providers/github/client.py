"""Получение снимков GitHub без исполнения кода проекта."""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

import httpx

from lovogit.cfg.github import GitHubSettings

from .archive import extract_archive
from .dto import ResolvedRepoDTO, SourceSnapshotDTO
from .errors import (
    AuthenticationError,
    InvalidArchive,
    InvalidRepoURL,
    InvalidSourceResponse,
    RateLimited,
    RepoNotFound,
    RevisionUnavailable,
    SourceStorageError,
    SourceUnavailable,
)
from .network_logging import private_request
from .urls import parse_github_url

_API = "https://api.github.com"


class GitHubProvider:
    def __init__(self, client: httpx.AsyncClient, settings: GitHubSettings):
        self._client = client
        self._settings = settings

    async def resolve(self, github_url: str) -> ResolvedRepoDTO:
        ref = parse_github_url(github_url)
        try:
            async with asyncio.timeout(self._settings.GITHUB_DEADLINE):
                metadata = await self._json(
                    f"{_API}/repos/{ref.owner}/{ref.name}", "metadata"
                )
                try:
                    repo_id = metadata["id"]
                    full_name = metadata["full_name"]
                    branch = metadata["default_branch"]
                    canonical = parse_github_url(f"https://github.com/{full_name}")
                    if (
                        type(repo_id) is not int
                        or repo_id <= 0
                        or not isinstance(full_name, str)
                    ):
                        raise ValueError
                except KeyError, ValueError, TypeError, InvalidRepoURL:
                    raise InvalidSourceResponse(stage="metadata") from None
                if not isinstance(branch, str) or not branch:
                    raise RevisionUnavailable(stage="revision")
                commit = await self._json(
                    f"{_API}/repos/{canonical.owner}/{canonical.name}/commits/{quote(branch, safe='')}",
                    "revision",
                )
                sha = commit.get("sha")
                if not isinstance(sha, str) or not re.fullmatch(
                    r"[0-9a-fA-F]{40}", sha
                ):
                    raise InvalidSourceResponse(stage="revision")
                return ResolvedRepoDTO(
                    repo_id, full_name, canonical.repo_url, branch, sha
                )
        except TimeoutError:
            raise SourceUnavailable(stage="resolve") from None

    @asynccontextmanager
    async def snapshot(self, repo: ResolvedRepoDTO):
        entered = False
        try:
            with TemporaryDirectory(
                prefix="lovogit-source-", dir=self._settings.GITHUB_WORKSPACE_DIR
            ) as directory:
                workspace = Path(directory)
                archive = workspace / "source.zip"
                root = workspace / "source"
                root.mkdir()
                await self._download(repo, archive)
                worker = asyncio.get_running_loop().run_in_executor(
                    None,
                    extract_archive,
                    archive,
                    root,
                    self._settings.GITHUB_MAX_EXTRACTED_BYTES,
                    self._settings.GITHUB_MAX_ENTRIES,
                )
                cancelled = False
                while True:
                    try:
                        await asyncio.shield(worker)
                        break
                    except asyncio.CancelledError:
                        cancelled = True
                        continue
                    except Exception:
                        if not cancelled:
                            raise
                        break
                if cancelled:
                    raise asyncio.CancelledError
                archive.unlink()
                entered = True
                yield SourceSnapshotDTO(repo, root)
        except TimeoutError:
            if entered:
                raise
            raise SourceUnavailable(stage="download") from None
        except OSError:
            if entered:
                raise
            raise SourceStorageError(stage="snapshot") from None

    async def _json(self, url: str, stage: str) -> dict:
        async with self._response(url, stage) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 1024 * 1024:
                    raise InvalidSourceResponse(stage=stage)
            try:
                result = json.loads(data)
            except ValueError, UnicodeError:
                raise InvalidSourceResponse(stage=stage) from None
            if not isinstance(result, dict):
                raise InvalidSourceResponse(stage=stage)
            return result

    async def _download(self, repo: ResolvedRepoDTO, destination: Path):
        ref = parse_github_url(repo.repo_url)
        if not re.fullmatch(r"[0-9a-fA-F]{40}", repo.commit_sha):
            raise InvalidSourceResponse(stage="revision")
        async with asyncio.timeout(self._settings.GITHUB_DEADLINE):
            async with self._response(
                f"{_API}/repos/{ref.owner}/{ref.name}/zipball/{repo.commit_sha}",
                "archive",
            ) as response:
                size = 0
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        size += len(chunk)
                        if size > self._settings.GITHUB_MAX_ARCHIVE_BYTES:
                            raise InvalidArchive(stage="download")
                        output.write(chunk)

    @asynccontextmanager
    async def _response(self, url: str, stage: str):
        response = None
        logging_token = private_request.set(True)
        try:
            for redirects in range(6):
                request = self._request(url)
                response = await self._client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if redirects == 5 or not location:
                        raise InvalidSourceResponse(stage="redirect")
                    url = str(response.url.join(location))
                    if not self._allowed_url(url):
                        raise InvalidSourceResponse(stage="redirect")
                    await response.aclose()
                    response = None
                    continue
                self._check_status(response, stage)
                yield response
                return
        except httpx.HTTPError:
            raise SourceUnavailable(stage=stage) from None
        finally:
            try:
                if response is not None:
                    await response.aclose()
            finally:
                private_request.reset(logging_token)

    def _request(self, url: str) -> httpx.Request:
        if not self._allowed_url(url):
            raise InvalidSourceResponse(stage="redirect")
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "LovoGit",
        }
        if urlsplit(url).netloc == "api.github.com":
            headers["Authorization"] = (
                "Bearer " + self._settings.GITHUB_TOKEN.get_secret_value()
            )
        timeout = httpx.Timeout(
            self._settings.GITHUB_READ_TIMEOUT,
            connect=self._settings.GITHUB_CONNECT_TIMEOUT,
        )
        return httpx.Request(
            "GET", url, headers=headers, extensions={"timeout": timeout.as_dict()}
        )

    @staticmethod
    def _allowed_url(value: str) -> bool:
        try:
            url = urlsplit(value)
            return (
                url.scheme == "https"
                and url.netloc in {"api.github.com", "codeload.github.com"}
                and not url.fragment
            )
        except ValueError:
            return False

    @classmethod
    def _check_status(cls, response: httpx.Response, stage: str):
        code = response.status_code
        kwargs = {"stage": stage, "status_code": code}
        if code == 429 or (
            code == 403
            and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or "retry-after" in response.headers
            )
        ):
            raise RateLimited(retry_at=cls._retry_at(response), **kwargs)
        if code == 401:
            raise AuthenticationError(**kwargs)
        if code == 403 or (code == 404 and stage == "metadata"):
            raise RepoNotFound(**kwargs)
        if code in {404, 409} and stage in {"revision", "archive"}:
            raise RevisionUnavailable(**kwargs)
        if code >= 500:
            raise SourceUnavailable(**kwargs)
        if code != 200:
            raise InvalidSourceResponse(**kwargs)

    @staticmethod
    def _retry_at(response: httpx.Response) -> datetime | None:
        retry = response.headers.get("retry-after")
        try:
            if retry is not None:
                if retry.isdigit():
                    return datetime.now(UTC) + timedelta(seconds=int(retry))
                return parsedate_to_datetime(retry).astimezone(UTC)
            reset = response.headers.get("x-ratelimit-reset")
            if reset:
                return datetime.fromtimestamp(int(reset), UTC)
        except ValueError, OverflowError, TypeError:
            pass
        return None
