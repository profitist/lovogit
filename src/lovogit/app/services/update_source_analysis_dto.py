from dataclasses import dataclass
from typing import Literal

from lovogit.app.services.update_source_dto import SourceLinkDTO
from lovogit.app.services.update_source_fetch_dto import UpdateSourceFetchResultDTO


@dataclass(frozen=True)
class ChangeEvidenceDTO:
    source_id: str
    quote: str
    start: int
    end: int
    links: tuple[SourceLinkDTO, ...]
    final_url: str | None


@dataclass(frozen=True)
class UpdateChangeDTO:
    category: str
    description: str
    symbol: str | None
    release_version: str | None
    evidence: tuple[ChangeEvidenceDTO, ...]
    version_evidence: ChangeEvidenceDTO | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RejectedChangeDTO:
    source_id: str
    index: int
    reason: str


@dataclass(frozen=True)
class DocumentAnalysisDTO:
    source_id: str
    status: Literal["processed", "skipped", "failed"]
    reason: str | None = None


@dataclass(frozen=True)
class UpdateSourceAnalysisResultDTO:
    fetched: UpdateSourceFetchResultDTO
    status: Literal["no_materials", "failed", "partial", "completed"]
    documents: tuple[DocumentAnalysisDTO, ...]
    changes: tuple[UpdateChangeDTO, ...] = ()
    unscoped_changes: tuple[UpdateChangeDTO, ...] = ()
    rejected: tuple[RejectedChangeDTO, ...] = ()
    observed_versions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "coverage_undetermined",
        "quotes_do_not_prove_interpretation",
        "empty_changes_do_not_prove_compatibility",
    )
    coverage: Literal["undetermined"] = "undetermined"
