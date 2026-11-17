from dataclasses import dataclass
from typing import Literal

from lovogit.app.providers.pypi_dto import PackageInfoDTO

LinkCategory = Literal[
    "changelog", "release_notes", "source", "documentation", "homepage", "other"
]


@dataclass(frozen=True)
class UpdateSourceRequest:
    package: str
    current_version: str
    target_version: str


@dataclass(frozen=True)
class SourceOriginDTO:
    role: Literal["current", "target"]
    package: str
    version: str
    requested_version: str
    source_kind: Literal["pypi_metadata"] = "pypi_metadata"


@dataclass(frozen=True)
class SourceLinkDTO:
    origin: SourceOriginDTO
    field: Literal["project_urls", "home_page"]
    label: str
    url: str
    category: LinkCategory
    exclusion_reason: (
        Literal[
            "empty_url", "invalid_url", "unsupported_scheme", "credentials_not_allowed"
        ]
        | None
    ) = None


@dataclass(frozen=True)
class ReleaseSourcesDTO:
    origin: SourceOriginDTO
    metadata: PackageInfoDTO
    description_available: bool
    links: tuple[SourceLinkDTO, ...]

    @property
    def requires_dist_dynamic(self) -> bool:
        return "requires_dist" in self.metadata.dynamic

    @property
    def has_change_links(self) -> bool:
        return any(
            link.category in {"changelog", "release_notes"}
            and link.exclusion_reason is None
            for link in self.links
        )


@dataclass(frozen=True)
class RequirementDifferenceDTO:
    current_requires_python: str | None
    target_requires_python: str | None
    added_requires_dist: tuple[str, ...]
    removed_requires_dist: tuple[str, ...]

    @property
    def requires_python_changed(self) -> bool:
        return self.current_requires_python != self.target_requires_python


@dataclass(frozen=True)
class UpdateSourcesDTO:
    request: UpdateSourceRequest
    current: ReleaseSourcesDTO
    target: ReleaseSourcesDTO
    requirements_diff: RequirementDifferenceDTO

    @property
    def has_change_links(self) -> bool:
        return self.current.has_change_links or self.target.has_change_links


from dataclasses import dataclass
from typing import Literal


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


from dataclasses import dataclass
from typing import Literal


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
