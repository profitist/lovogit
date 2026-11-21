import tomllib
from dataclasses import dataclass, replace

from packaging.markers import Marker
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from lovogit.app.dependencies.dto import DiagnosticDTO, LockCandidateDTO, ProvenanceDTO
from lovogit.app.dependencies.files import InventoryFileError, SnapshotReader


@dataclass(frozen=True)
class LockDocument:
    data: dict
    root_index: int | None
    diagnostics: tuple[DiagnosticDTO, ...] = ()


def read_uv_lock(reader: SnapshotReader, pyproject: dict) -> LockDocument:
    origin = ProvenanceDTO("uv.lock")
    try:
        data = tomllib.loads(reader.read("uv.lock", origin).decode("utf-8-sig"))
    except (
        InventoryFileError,
        ValueError,
        RecursionError,
    ) as error:
        return LockDocument(
            {},
            None,
            (
                DiagnosticDTO(
                    error.code
                    if isinstance(error, InventoryFileError)
                    else "invalid_lock_toml",
                    origin,
                ),
            ),
        )
    if (
        type(data.get("version")) is not int
        or type(data.get("revision")) is not int
        or (data["version"], data["revision"]) != (1, 3)
    ):
        return LockDocument(
            data, None, (DiagnosticDTO("unsupported_lock_version", origin),)
        )
    tool = pyproject.get("tool", {})
    uv = tool.get("uv", {}) if isinstance(tool, dict) else {}
    manifest = data.get("manifest", {})
    if not isinstance(uv, dict) or not isinstance(manifest, dict):
        return LockDocument(
            data, None, (DiagnosticDTO("unsupported_lock_metadata", origin),)
        )
    if "workspace" in uv or "members" in manifest:
        return LockDocument(
            data, None, (DiagnosticDTO("unsupported_workspace", origin),)
        )
    packages = data.get("package")
    if not isinstance(packages, list) or any(
        not isinstance(item, dict) for item in packages
    ):
        return LockDocument(
            data, None, (DiagnosticDTO("invalid_lock_packages", origin),)
        )
    try:
        reader.consume_records(len(packages), ProvenanceDTO("uv.lock", "package"))
    except InventoryFileError as error:
        return LockDocument(data, None, (DiagnosticDTO(error.code, error.origin),))
    roots = [
        index
        for index, item in enumerate(packages)
        if isinstance(item.get("source"), dict)
        and item["source"] in ({"virtual": "."}, {"editable": "."})
    ]
    if len(roots) != 1:
        return LockDocument(
            data,
            None,
            (
                DiagnosticDTO(
                    "ambiguous_lock_root" if roots else "missing_lock_root", origin
                ),
            ),
        )
    return LockDocument(data, roots[0])


def validate_lock_metadata(
    document: LockDocument, pyproject: dict, dependencies, reader: SnapshotReader
):

    origin = ProvenanceDTO("uv.lock", "package.metadata")
    if document.diagnostics:
        return document.diagnostics
    project = pyproject.get("project", {})
    tool = pyproject.get("tool", {})
    uv = tool.get("uv", {}) if isinstance(tool, dict) else {}
    if not isinstance(project, dict) or not isinstance(uv, dict):
        return (DiagnosticDTO("unsupported_lock_metadata", origin),)
    if uv:
        return (
            DiagnosticDTO(
                "unsupported_uv_configuration",
                ProvenanceDTO("pyproject.toml", "tool.uv"),
            ),
        )
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(
        field in dynamic for field in ("dependencies", "optional-dependencies")
    ):
        return (DiagnosticDTO("dynamic_lock_metadata", origin),)
    root = document.data["package"][document.root_index]
    metadata = root.get("metadata", {})
    rows = metadata.get("requires-dist") if isinstance(metadata, dict) else None
    if not isinstance(rows, list):
        return (DiagnosticDTO("unsupported_lock_metadata", origin),)
    try:
        reader.consume_records(len(rows), origin)
        if canonicalize_name(root["name"], validate=True) != canonicalize_name(
            project["name"], validate=True
        ):
            return (DiagnosticDTO("lock_metadata_mismatch", origin),)
        if str(SpecifierSet(document.data.get("requires-python", ""))) != str(
            SpecifierSet(project.get("requires-python", ""))
        ):
            return (
                DiagnosticDTO(
                    "lock_metadata_mismatch",
                    ProvenanceDTO("uv.lock", "requires-python"),
                ),
            )
        expected = set()
        for item in dependencies:
            if item.source_kind != "registry":
                return (DiagnosticDTO("unsupported_lock_metadata", origin),)
            marker = item.marker
            if item.scope.startswith("extra:"):
                extra = item.scope.removeprefix("extra:")
                marker = (
                    f'({marker}) and extra == "{extra}"'
                    if marker
                    else f'extra == "{extra}"'
                )
            expected.add(
                (
                    item.name,
                    item.extras,
                    item.specifier,
                    str(Marker(marker)) if marker else None,
                )
            )
        actual = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) - {
                "name",
                "extras",
                "specifier",
                "marker",
            }:
                return (DiagnosticDTO("unsupported_lock_metadata", origin),)
            extras = row.get("extras", [])
            if not isinstance(extras, list) or any(
                not isinstance(extra, str) for extra in extras
            ):
                return (DiagnosticDTO("unsupported_lock_metadata", origin),)
            marker = row.get("marker")
            actual.add(
                (
                    canonicalize_name(row["name"], validate=True),
                    tuple(sorted(canonicalize_name(extra) for extra in extras)),
                    str(SpecifierSet(row.get("specifier", ""))),
                    str(Marker(marker)) if marker else None,
                )
            )
    except InventoryFileError as error:
        return (DiagnosticDTO(error.code, error.origin),)
    except ValueError, TypeError, KeyError, RecursionError:
        return (DiagnosticDTO("unsupported_lock_metadata", origin),)
    if expected != actual:
        return (DiagnosticDTO("lock_metadata_mismatch", origin),)
    return ()


def apply_uv_lock(
    dependencies,
    document: LockDocument,
    pyproject: dict,
    reader: SnapshotReader,
    *,
    manifest_complete: bool = True,
):

    diagnostics = list(
        validate_lock_metadata(document, pyproject, dependencies, reader)
    )
    if not manifest_complete:
        diagnostics.append(
            DiagnosticDTO("incomplete_manifest", ProvenanceDTO("pyproject.toml"))
        )
    by_name = {}
    packages = document.data.get("package", [])
    if not isinstance(packages, list):
        packages = []
    for index, package in enumerate(packages):
        if not isinstance(package, dict) or index == document.root_index:
            continue
        origin = ProvenanceDTO("uv.lock", f"package[{index}]")
        try:
            name = canonicalize_name(package["name"], validate=True)
        except ValueError, TypeError, KeyError, RecursionError:
            diagnostics.append(DiagnosticDTO("invalid_lock_entry", origin))
            continue
        source = package.get("source", {})
        markers = package.get("resolution-markers", [])
        if (
            not isinstance(source, dict)
            or any(not isinstance(value, str) for value in source.values())
            or not isinstance(markers, list)
            or any(not isinstance(value, str) for value in markers)
        ):
            diagnostics.append(DiagnosticDTO("invalid_lock_entry", origin))
            continue
        candidate = LockCandidateDTO(
            package.get("version") if isinstance(package.get("version"), str) else None,
            tuple(sorted(source.items())),
            tuple(markers),
            origin,
        )
        by_name.setdefault(name, []).append(candidate)
    root = packages[document.root_index] if document.root_index is not None else {}
    root_edges = root.get("dependencies", [])
    optional = root.get("optional-dependencies", {})
    if (
        not isinstance(root_edges, list)
        or not isinstance(optional, dict)
        or any(not isinstance(values, list) for values in optional.values())
    ):
        diagnostics.append(
            DiagnosticDTO(
                "unsupported_lock_edges",
                ProvenanceDTO("uv.lock", "package.dependencies"),
            )
        )
        root_edges, optional = [], {}
    try:
        reader.consume_records(
            len(root_edges) + sum(len(values) for values in optional.values()),
            ProvenanceDTO("uv.lock", "package.dependencies"),
        )
    except InventoryFileError as error:
        diagnostics.append(DiagnosticDTO(error.code, error.origin))
    edge_index = {}
    edge_groups = [("runtime", root_edges)]
    edge_groups.extend(
        ("extra:" + canonicalize_name(name), edges) for name, edges in optional.items()
    )
    for scope, edges in edge_groups:
        for edge in edges:
            try:
                if not isinstance(edge, dict):
                    raise TypeError("Lock dependency edge must be a table")
                name = canonicalize_name(edge["name"], validate=True)
            except ValueError, TypeError, KeyError:
                diagnostics.append(
                    DiagnosticDTO(
                        "unsupported_lock_edges",
                        ProvenanceDTO("uv.lock", "package.dependencies"),
                    )
                )
                continue
            edge_index.setdefault((scope, name), []).append(edge)
    global_reason = diagnostics[0].code if diagnostics else None
    result = []
    for item in dependencies:
        candidates = tuple(by_name.get(item.name, ()))
        base = replace(item, lock_candidates=candidates)
        if item.version_state in {"unsupported", "conflict"}:
            result.append(base)
            continue
        reason = global_reason
        state = "unresolved"
        chosen = None
        edges = edge_index.get((item.scope, item.name), [])
        if not reason:
            reason, state, chosen = _resolve_locked(
                item, candidates, edges, document.data
            )
        if reason:
            result.append(
                replace(base, version_state=state, current_version=None, reason=reason)
            )
            diagnostics.append(DiagnosticDTO(reason, item.origins[0]))
        else:
            result.append(
                replace(
                    base,
                    version_state="locked",
                    current_version=chosen.version,
                    reason=None,
                    origins=tuple(sorted(set(item.origins + (chosen.origin,)))),
                )
            )
    return tuple(result), tuple(dict.fromkeys(diagnostics))


def _resolve_locked(item, candidates, edges, data):

    matching = []
    for edge in edges:
        if not isinstance(edge, dict):
            return "unsupported_lock_edges", "unresolved", None
        try:
            name = canonicalize_name(edge["name"], validate=True)
        except ValueError, TypeError, KeyError, RecursionError:
            return "unsupported_lock_edges", "unresolved", None
        if name == item.name:
            if set(edge) - {
                "name",
                "extra",
                "version",
                "source",
                "marker",
                "resolution-markers",
            }:
                return "unsupported_lock_edges", "unresolved", None
            extras = edge.get("extra", [])
            if not isinstance(extras, list) or any(
                not isinstance(extra, str) for extra in extras
            ):
                return "unsupported_lock_edges", "unresolved", None
            if (
                tuple(sorted(canonicalize_name(extra) for extra in extras))
                == item.extras
            ):
                matching.append(edge)
    if not matching or not candidates:
        return "missing_lock_entry", "unresolved", None
    if (
        data.get("resolution-markers")
        or any(
            edge.get("marker") or edge.get("resolution-markers") for edge in matching
        )
        or any(candidate.markers for candidate in candidates)
    ):
        return "conditional_lock", "unresolved", None
    linked = []
    for candidate in candidates:
        for edge in matching:
            if "version" in edge and edge["version"] != candidate.version:
                continue
            if "source" in edge and edge["source"] != dict(candidate.source):
                continue
            linked.append(candidate)
            break
    if not linked:
        return "missing_lock_entry", "unresolved", None
    if any(set(dict(candidate.source)) != {"registry"} for candidate in linked):
        return "unsupported_lock_source", "unresolved", None
    if len({candidate.source for candidate in linked}) != 1:
        return "ambiguous_lock_source", "unresolved", None
    try:
        versions = {Version(candidate.version) for candidate in linked}
    except ValueError, TypeError:
        return "invalid_lock_version", "unresolved", None
    if len(versions) != 1:
        return "ambiguous_lock", "unresolved", None
    chosen = linked[0]
    if not all(
        SpecifierSet(text).contains(chosen.version, prereleases=True)
        for text in (item.specifier, *item.constraint_specifiers)
    ):
        return "lock_version_conflict", "conflict", None
    return None, "locked", chosen
