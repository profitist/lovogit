from packaging.utils import canonicalize_name

from lovogit.app.dependencies.dto import (
    DiagnosticDTO,
    ParsedDeclarationsDTO,
    ProvenanceDTO,
)
from lovogit.app.dependencies.files import InventoryFileError, SnapshotReader
from lovogit.app.dependencies.versions import parse_requirement


def parse_pyproject(data: dict, reader: SnapshotReader) -> ParsedDeclarationsDTO:
    origin = ProvenanceDTO("pyproject.toml", "project")
    project = data.get("project")
    if not isinstance(project, dict):
        return ParsedDeclarationsDTO(
            diagnostics=(DiagnosticDTO("invalid_project", origin, "error"),),
            failed=True,
        )
    dependencies, diagnostics = [], []
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(
        not isinstance(item, str) for item in dynamic
    ):
        diagnostics.append(
            DiagnosticDTO(
                "invalid_dynamic", ProvenanceDTO("pyproject.toml", "project.dynamic")
            )
        )
        dynamic = ["dependencies", "optional-dependencies"]
    if "dependency-groups" in data:
        diagnostics.append(
            DiagnosticDTO(
                "unsupported_dependency_groups",
                ProvenanceDTO("pyproject.toml", "dependency-groups"),
            )
        )
    sections = [("runtime", "project.dependencies", project.get("dependencies", []))]
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        diagnostics.append(
            DiagnosticDTO(
                "invalid_optional_dependencies",
                ProvenanceDTO("pyproject.toml", "project.optional-dependencies"),
            )
        )
    else:
        for name, requirements in optional.items():
            try:
                scope = "extra:" + canonicalize_name(name, validate=True)
            except ValueError:
                diagnostics.append(
                    DiagnosticDTO(
                        "invalid_extra",
                        ProvenanceDTO(
                            "pyproject.toml", f"project.optional-dependencies.{name}"
                        ),
                    )
                )
                continue
            sections.append(
                (scope, f"project.optional-dependencies.{name}", requirements)
            )
    for field in ("dependencies", "optional-dependencies"):
        if field in dynamic:
            diagnostics.append(
                DiagnosticDTO(
                    "dynamic_dependencies",
                    ProvenanceDTO("pyproject.toml", f"project.{field}"),
                )
            )
    try:
        for scope, key, values in sections:
            if key.split(".")[1] in dynamic:
                continue
            section_origin = ProvenanceDTO("pyproject.toml", key)
            if not isinstance(values, list):
                diagnostics.append(
                    DiagnosticDTO("invalid_dependency_list", section_origin)
                )
                continue
            for index, raw in enumerate(values):
                item_origin = ProvenanceDTO("pyproject.toml", f"{key}[{index}]")
                reader.consume_records(1, item_origin)
                if not isinstance(raw, str):
                    diagnostics.append(
                        DiagnosticDTO("invalid_requirement", item_origin)
                    )
                    continue
                try:
                    item = parse_requirement(raw, item_origin, scope=scope)
                except ValueError:
                    diagnostics.append(
                        DiagnosticDTO("invalid_requirement", item_origin)
                    )
                    continue
                dependencies.append(item)
                if item.version_state == "unsupported":
                    diagnostics.append(DiagnosticDTO("unsupported_source", item_origin))
    except InventoryFileError as error:
        diagnostics.append(DiagnosticDTO(error.code, error.origin))
    return ParsedDeclarationsDTO(tuple(dependencies), diagnostics=tuple(diagnostics))
