from dataclasses import dataclass
from typing import Literal

from lovogit.app.providers.github.dto import ResolvedRepoDTO

InventoryStatus = Literal["complete", "partial", "failed", "no_sources"]
VersionState = Literal[
    "declared_pin", "locked", "unresolved", "conflict", "unsupported"
]
SourceChoice = Literal["auto", "pyproject", "requirements"]


@dataclass(frozen=True)
class InventoryLimits:
    max_files: int = 128
    max_depth: int = 16
    max_file_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 32 * 1024 * 1024
    max_records: int = 100_000

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(
                    f"Dependency inventory requires positive integer {name}"
                )


@dataclass(frozen=True, order=True)
class ProvenanceDTO:
    path: str
    key: str = ""
    start_line: int = 0
    end_line: int = 0


@dataclass(frozen=True)
class DiagnosticDTO:
    code: str
    origin: ProvenanceDTO
    severity: Literal["warning", "error"] = "warning"


@dataclass(frozen=True)
class DiscoveredFileDTO:
    path: str
    disposition: Literal["selected", "not_selected", "unsupported"]


@dataclass(frozen=True)
class ReadFileDTO:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class LockCandidateDTO:
    version: str | None
    source: tuple[tuple[str, str], ...]
    markers: tuple[str, ...]
    origin: ProvenanceDTO


@dataclass(frozen=True)
class DependencyDeclarationDTO:
    name: str
    original_name: str
    raw_requirement: str
    specifier: str
    extras: tuple[str, ...]
    marker: str | None
    scope: str
    origins: tuple[ProvenanceDTO, ...]
    source_kind: Literal["registry", "direct_url", "editable"] = "registry"
    hashes: tuple[str, ...] = ()
    version_state: VersionState = "unresolved"
    current_version: str | None = None
    reason: str | None = "no_exact_version"
    constraint_origins: tuple[ProvenanceDTO, ...] = ()
    constraint_specifiers: tuple[str, ...] = ()
    lock_candidates: tuple[LockCandidateDTO, ...] = ()


@dataclass(frozen=True)
class DependencyInventoryDTO:
    repo: ResolvedRepoDTO
    source: Literal["pyproject", "requirements"] | None
    status: InventoryStatus
    discovered_files: tuple[DiscoveredFileDTO, ...] = ()
    read_files: tuple[ReadFileDTO, ...] = ()
    dependencies: tuple[DependencyDeclarationDTO, ...] = ()
    constraints: tuple[DependencyDeclarationDTO, ...] = ()
    diagnostics: tuple[DiagnosticDTO, ...] = ()


@dataclass(frozen=True)
class ParsedDeclarationsDTO:
    dependencies: tuple[DependencyDeclarationDTO, ...] = ()
    constraints: tuple[DependencyDeclarationDTO, ...] = ()
    diagnostics: tuple[DiagnosticDTO, ...] = ()
    failed: bool = False
