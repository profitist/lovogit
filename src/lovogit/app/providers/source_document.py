import asyncio
import unicodedata
from email.message import Message
from urllib.parse import urlsplit

import httpcore
import httpx

from lovogit.app.providers.source_document_transport import (
    BlockedDestination,
    SourceDocumentTransport,
)
from lovogit.app.update_sources.dto import FetchedSourceDTO
from lovogit.cfg.source_fetch import SourceFetchSettings


def _valid_url(value: str) -> bool:
    if "\\" in value or any(
        c.isspace() or unicodedata.category(c) == "Cc" for c in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 80 if parsed.scheme.lower() == "http" else 443)
        )
    except ValueError:
        return False


class SourceDocumentLoader:
    def __init__(self, settings: SourceFetchSettings, transport=None):
        self._settings = settings
        self._client = httpx.AsyncClient(
            transport=transport or SourceDocumentTransport(), trust_env=False
        )

    async def aclose(self):
        await self._client.aclose()

    async def fetch(self, url: str) -> FetchedSourceDTO:
        current = url
        chain = []
        code = None
        retry_after = None
        content_type = None
        encoding = None
        reason = None
        body = bytearray()
        try:
            async with asyncio.timeout(self._settings.SOURCE_FETCH_SOURCE_DEADLINE):
                while True:
                    if not _valid_url(current):
                        reason = "invalid_url" if not chain else "invalid_redirect"
                        break
                    request = httpx.Request(
                        "GET",
                        current,
                        headers={
                            "User-Agent": "LovoGit",
                            "Accept": "text/plain, text/markdown, text/html",
                            "Accept-Encoding": "identity",
                        },
                        extensions={
                            "timeout": httpx.Timeout(
                                self._settings.SOURCE_FETCH_READ_TIMEOUT,
                                connect=self._settings.SOURCE_FETCH_CONNECT_TIMEOUT,
                            ).as_dict()
                        },
                    )
                    response = await self._client.send(
                        request, stream=True, auth=None, follow_redirects=False
                    )
                    try:
                        code = response.status_code
                        retry_after = response.headers.get("retry-after")
                        length = response.headers.get("content-length")
                        if length and length.isascii() and length.isdecimal():
                            digits = length.lstrip("0") or "0"
                            limit = str(self._settings.SOURCE_FETCH_MAX_RESPONSE_BYTES)
                            if len(digits) > len(limit) or (
                                len(digits) == len(limit) and digits > limit
                            ):
                                reason = "size_limit"
                                break
                        if code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                reason = "invalid_redirect"
                                break
                            try:
                                target = str(
                                    httpx.URL(current)
                                    .join(location)
                                    .copy_with(fragment=None)
                                )
                            except httpx.InvalidURL, ValueError:
                                reason = "invalid_redirect"
                                break
                            if not _valid_url(target) or (
                                current.lower().startswith("https:")
                                and target.lower().startswith("http:")
                            ):
                                reason = "invalid_redirect"
                                break
                            if (
                                target in chain
                                or target == current
                                or len(chain)
                                >= self._settings.SOURCE_FETCH_MAX_REDIRECTS
                            ):
                                reason = "redirect_limit"
                                break
                            chain.append(current)
                            current = target
                            continue
                        if code != 200:
                            reason = "http_error"
                            break
                        content_type = response.headers.get("content-type", "")
                        message = Message()
                        message["content-type"] = content_type
                        if (
                            message.get_content_type().lower()
                            not in {"text/plain", "text/markdown", "text/html"}
                            or not content_type
                        ):
                            reason = "unsupported_content_type"
                            break
                        if (
                            response.headers.get("content-encoding", "identity").lower()
                            != "identity"
                        ):
                            reason = "unsupported_content_encoding"
                            break
                        async for chunk in response.aiter_raw():
                            body.extend(chunk)
                            if (
                                len(body)
                                > self._settings.SOURCE_FETCH_MAX_RESPONSE_BYTES
                            ):
                                reason = "size_limit"
                                break
                        encoding = message.get_content_charset() or "utf-8"
                        break
                    finally:
                        await response.aclose()
        except BlockedDestination:
            reason = "blocked_destination"
        except httpx.InvalidURL:
            reason = "invalid_url" if not chain else "invalid_redirect"
        except TimeoutError, httpx.TimeoutException, httpcore.TimeoutException:
            reason = "timeout"
        except (
            httpx.HTTPError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            OSError,
        ):
            reason = "transport_error"
        text = None
        if reason is None:
            try:
                text = bytes(body).decode(encoding)
                if not text.strip():
                    reason, text = "empty_content", None
            except LookupError:
                reason = "unsupported_encoding"
            except UnicodeDecodeError:
                reason = "decode_error"
        return FetchedSourceDTO(
            links=(),
            status="failed" if reason else "fetched",
            reason=reason,
            requested_url=url,
            final_url=current,
            redirect_chain=tuple(chain),
            status_code=code,
            retry_after=retry_after,
            content_type=content_type,
            encoding=encoding,
            text=text,
        )
