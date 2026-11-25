from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from lovogit.app.dependencies.dto import DependencyInventoryDTO, SourceChoice
from lovogit.app.providers.github.dto import ResolvedRepoDTO
from lovogit.app.update_selection import UpdateSelectionDTO
from lovogit.app.update_sources.dto import (
    UpdateSourceAnalysisResultDTO,
    UpdateSourceFetchResultDTO,
    UpdateSourcesDTO,
)

RunStatus = Literal["completed", "partial", "failed", "skipped", "no_update"]
STAGES = (
    "snapshot",
    "inventory",
    "selection",
    "collection",
    "fetching",
    "extraction",
    "index",
    "compare",
)


@dataclass(frozen=True)
class ProjectUpdateRequest:
    repo_url: str
    package: str
    source: SourceChoice = "auto"


@dataclass
class StageResultDTO:
    outcome: str = "not_run"
    reason: str = "previous_stage_not_completed"


@dataclass
class ChangeSearchDTO:
    change_id: str
    outcome: str = "not_run"
    reason: str | None = None
    context: dict = field(default_factory=dict)
    locations: list[dict] = field(default_factory=list)
    related_tests: list[dict] = field(default_factory=list)


@dataclass
class ProjectUpdateResultDTO:
    run_id: str
    request: ProjectUpdateRequest
    format_version: int = 1
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ended_at: str | None = None
    status: RunStatus = "failed"
    reason: str | None = None
    repo: ResolvedRepoDTO | None = None
    inventory: DependencyInventoryDTO | None = None
    selection: UpdateSelectionDTO | None = None
    collection: UpdateSourcesDTO | None = None
    fetching: UpdateSourceFetchResultDTO | None = None
    extraction: UpdateSourceAnalysisResultDTO | None = None
    searches: list[ChangeSearchDTO] = field(default_factory=list)
    stages: dict[str, StageResultDTO] = field(
        default_factory=lambda: {name: StageResultDTO() for name in STAGES}
    )
    limitations: list[str] = field(
        default_factory=lambda: [
            "coverage_undetermined",
            "quotes_do_not_prove_interpretation",
            "empty_search_does_not_prove_compatibility",
            "tests_not_executed",
        ]
    )
    diagnostics: list[str] = field(default_factory=list)
    export: dict = field(default_factory=dict)


def analysis_status(result: ProjectUpdateResultDTO) -> RunStatus:
    if result.selection and result.selection.status in {"skipped", "no_update"}:
        return result.selection.status
    if result.reason in {"dependency_not_found", "ambiguous_dependency", "no_sources"}:
        return "skipped"
    if result.extraction is None or result.extraction.status == "failed":
        return "failed"
    incomplete = (
        result.reason is not None
        or result.inventory is not None
        and result.inventory.status == "partial"
        or result.extraction.status in {"partial", "no_materials"}
        or any(search.outcome != "completed" for search in result.searches)
    )
    return "partial" if incomplete else "completed"
