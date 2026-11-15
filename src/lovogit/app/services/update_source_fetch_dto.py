from dataclasses import dataclass
from typing import Literal

from lovogit.app.services.update_source_dto import SourceLinkDTO, UpdateSourcesDTO


@dataclass(frozen=True)
class FetchedSourceDTO:
    links: tuple[SourceLinkDTO, ...]
    status: Literal["fetched", "skipped", "failed"]
    reason: str | None = None
    requested_url: str | None = None
    final_url: str | None = None
    redirect_chain: tuple[str, ...] = ()
    status_code: int | None = None
    retry_after: str | None = None
    content_type: str | None = None
    encoding: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class UpdateSourceFetchResultDTO:
    sources: UpdateSourcesDTO
    has_candidates: bool
    results: tuple[FetchedSourceDTO, ...]
