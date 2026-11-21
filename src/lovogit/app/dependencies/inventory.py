import tomllib
from dataclasses import dataclass

from lovogit.app.dependencies.dto import (
    DependencyInventoryDTO,
    DiagnosticDTO,
    DiscoveredFileDTO,
    InventoryLimits,
    ProvenanceDTO,
    SourceChoice,
)
from lovogit.app.dependencies.files import InventoryFileError, SnapshotReader
from lovogit.app.dependencies.pyproject import parse_pyproject
from lovogit.app.dependencies.requirements import parse_requirements
from lovogit.app.dependencies.uv_lock import apply_uv_lock, read_uv_lock
from lovogit.app.dependencies.versions import classify_versions
from lovogit.app.providers.github.dto import SourceSnapshotDTO


@dataclass(frozen=True)
class SourceSelection:
    source: str | None
    files: tuple[DiscoveredFileDTO, ...]
    pyproject: dict | None
    diagnostics: tuple[DiagnosticDTO, ...] = ()
    failed: bool = False


def select_source(
    reader: SnapshotReader, source: SourceChoice = "auto"
) -> SourceSelection:
    if source not in {"auto", "pyproject", "requirements"}:
        raise ValueError(f"Cannot select dependency source {source!r}")
    found = [
        name
        for name in (
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            "poetry.lock",
            "Pipfile",
            "setup.py",
            "setup.cfg",
        )
        if reader.exists(name)
    ]
    selected = None
    data = None
    diagnostics = []
    failed = False
    if source == "pyproject" or (source == "auto" and "pyproject.toml" in found):
        origin = ProvenanceDTO("pyproject.toml")
        try:
            data = tomllib.loads(
                reader.read("pyproject.toml", origin).decode("utf-8-sig")
            )
        except (
            InventoryFileError,
            ValueError,
            RecursionError,
        ) as error:
            diagnostics.append(
                DiagnosticDTO(
                    error.code
                    if isinstance(error, InventoryFileError)
                    else "invalid_toml",
                    origin,
                    "error",
                )
            )
            selected, failed = "pyproject", True
        else:
            if "project" in data:
                selected = "pyproject"
            elif source == "pyproject":
                diagnostics.append(
                    DiagnosticDTO("unsupported_pyproject", origin, "error")
                )
                selected, failed = "pyproject", True
            elif isinstance(data.get("tool"), dict) and "poetry" in data["tool"]:
                diagnostics.append(DiagnosticDTO("unsupported_poetry", origin))
    if (
        selected is None
        and source != "pyproject"
        and ("requirements.txt" in found or source == "requirements")
    ):
        selected = "requirements"
        if "requirements.txt" not in found:
            diagnostics.append(
                DiagnosticDTO(
                    "missing_source", ProvenanceDTO("requirements.txt"), "error"
                )
            )
            failed = True
    files = []
    for name in found:
        if name in {"poetry.lock", "Pipfile", "setup.py", "setup.cfg"}:
            disposition = "unsupported"
        elif name == {
            "pyproject": "pyproject.toml",
            "requirements": "requirements.txt",
        }.get(selected) or (name == "uv.lock" and selected == "pyproject"):
            disposition = "selected"
        else:
            disposition = "not_selected"
        files.append(DiscoveredFileDTO(name, disposition))
    return SourceSelection(selected, tuple(files), data, tuple(diagnostics), failed)


def read_dependency_inventory(
    snapshot: SourceSnapshotDTO,
    *,
    source: SourceChoice = "auto",
    limits: InventoryLimits | None = None,
) -> DependencyInventoryDTO:

    reader = SnapshotReader(snapshot.root, limits or InventoryLimits())
    try:
        selection = select_source(reader, source)
    except InventoryFileError as error:
        return DependencyInventoryDTO(
            snapshot.repo,
            None,
            "failed",
            read_files=reader.files,
            diagnostics=(DiagnosticDTO(error.code, error.origin, "error"),),
        )
    if selection.failed or selection.source is None:
        diagnostics = list(selection.diagnostics)
        if selection.source is None:
            diagnostics.extend(
                DiagnosticDTO("unsupported_source_format", ProvenanceDTO(item.path))
                for item in selection.files
                if item.disposition == "unsupported"
            )
        return DependencyInventoryDTO(
            snapshot.repo,
            selection.source,
            "failed" if selection.failed else "no_sources",
            selection.files,
            reader.files,
            diagnostics=tuple(diagnostics),
        )
    parsed = (
        parse_pyproject(selection.pyproject, reader)
        if selection.source == "pyproject"
        else parse_requirements(reader)
    )
    dependencies = classify_versions(parsed.dependencies, parsed.constraints)
    diagnostics = [*selection.diagnostics, *parsed.diagnostics]
    partial = bool(parsed.diagnostics)
    unsupported = [item for item in dependencies if item.version_state == "unsupported"]
    for item in unsupported:
        diagnostics.append(DiagnosticDTO(item.reason, item.origins[0]))
    partial = partial or bool(unsupported)
    if (
        selection.source == "pyproject"
        and not parsed.failed
        and any(
            item.path == "uv.lock" and item.disposition == "selected"
            for item in selection.files
        )
    ):
        document = read_uv_lock(reader, selection.pyproject)
        dependencies, lock_diagnostics = apply_uv_lock(
            dependencies,
            document,
            selection.pyproject,
            reader,
            manifest_complete=not parsed.diagnostics,
        )
        diagnostics.extend(lock_diagnostics)
        partial = partial or bool(lock_diagnostics)
    status = "failed" if parsed.failed else "partial" if partial else "complete"
    return DependencyInventoryDTO(
        snapshot.repo,
        selection.source,
        status,
        selection.files,
        reader.files,
        dependencies,
        parsed.constraints,
        tuple(
            sorted(
                set(diagnostics),
                key=lambda item: (item.origin, item.code, item.severity),
            )
        ),
    )
