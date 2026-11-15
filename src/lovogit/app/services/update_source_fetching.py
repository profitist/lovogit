import asyncio
from dataclasses import replace

import httpx

from lovogit.app.services.update_source_dto import SourceLinkDTO, UpdateSourcesDTO
from lovogit.app.services.update_source_fetch_dto import (
    FetchedSourceDTO,
    UpdateSourceFetchResultDTO,
)
from lovogit.cfg.source_fetch import SourceFetchSettings


class UpdateSourceFetchService:
    def __init__(self, loader, settings: SourceFetchSettings | None = None):
        self._loader = loader
        self._settings = settings or SourceFetchSettings()

    async def fetch(self, sources: UpdateSourcesDTO) -> UpdateSourceFetchResultDTO:
        entries: list[FetchedSourceDTO | str] = []
        groups: dict[str, list[SourceLinkDTO]] = {}
        for link in (*sources.current.links, *sources.target.links):
            if link.category not in {"changelog", "release_notes"}:
                reason = "not_change_link"
            elif link.exclusion_reason is not None:
                reason = link.exclusion_reason
            else:
                try:
                    url = str(httpx.URL(link.url).copy_with(fragment=None))
                except httpx.InvalidURL:
                    url = link.url
                if url not in groups:
                    groups[url] = []
                    entries.append(url)
                groups[url].append(link)
                continue
            entries.append(
                FetchedSourceDTO(
                    links=(link,),
                    status="skipped",
                    reason=reason,
                    requested_url=link.url,
                )
            )
        results = []
        deadline = (
            asyncio.get_running_loop().time() + self._settings.SOURCE_FETCH_DEADLINE
        )
        count = 0
        expired = False
        try:
            for entry in entries:
                if isinstance(entry, str):
                    links = tuple(groups[entry])
                    if expired or asyncio.get_running_loop().time() >= deadline:
                        expired = True
                        results.append(
                            FetchedSourceDTO(
                                links, "skipped", "deadline_exceeded", entry
                            )
                        )
                        continue
                    if count >= self._settings.SOURCE_FETCH_MAX_SOURCES:
                        results.append(
                            FetchedSourceDTO(links, "skipped", "source_limit", entry)
                        )
                        continue
                    count += 1
                    try:
                        async with asyncio.timeout_at(deadline):
                            document = await self._loader.fetch(entry)
                    except TimeoutError:
                        expired = True
                        document = FetchedSourceDTO((), "failed", "timeout", entry)
                    results.append(replace(document, links=tuple(groups[entry])))
                else:
                    results.append(entry)
        finally:
            await self._loader.aclose()
        return UpdateSourceFetchResultDTO(sources, bool(groups), tuple(results))
