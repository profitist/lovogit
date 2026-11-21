from collections import defaultdict
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from lovogit.app.dependencies.dto import DependencyDeclarationDTO, ProvenanceDTO


def parse_requirement(
    raw: str,
    origin: ProvenanceDTO,
    *,
    scope: str = "runtime",
    hashes: tuple[str, ...] = (),
    editable: bool = False,
) -> DependencyDeclarationDTO:
    try:
        requirement = Requirement(raw)
    except RecursionError:
        raise ValueError("invalid_requirement") from None
    except InvalidRequirement:
        egg = parse_qs(urlsplit(raw).fragment).get("egg", []) if "#egg=" in raw else []
        if len(egg) != 1:
            raise ValueError("invalid_requirement") from None
        try:
            requirement = Requirement(f"{egg[0]} @ {raw}")
        except InvalidRequirement, RecursionError:
            raise ValueError("invalid_requirement") from None
    kind = "editable" if editable else "direct_url" if requirement.url else "registry"
    return DependencyDeclarationDTO(
        name=canonicalize_name(requirement.name, validate=True),
        original_name=requirement.name,
        raw_requirement=raw,
        specifier=str(requirement.specifier),
        extras=tuple(sorted(canonicalize_name(extra) for extra in requirement.extras)),
        marker=str(requirement.marker) if requirement.marker else None,
        scope=scope,
        origins=(origin,),
        source_kind=kind,
        hashes=hashes,
        version_state="unsupported" if kind != "registry" else "unresolved",
        reason="unsupported_source" if kind != "registry" else "no_exact_version",
    )


def classify_versions(dependencies, constraints=()):

    grouped = {}
    for item in dependencies:
        key = (
            item.name,
            item.scope,
            item.marker,
            item.extras,
            item.specifier,
            item.source_kind,
            item.raw_requirement if item.source_kind != "registry" else "",
        )
        if key in grouped:
            previous = grouped[key]
            grouped[key] = replace(
                previous,
                origins=tuple(sorted(set(previous.origins + item.origins))),
                hashes=tuple(sorted(set(previous.hashes + item.hashes))),
            )
        else:
            grouped[key] = item
    records = list(grouped.values())
    by_scope = defaultdict(list)
    by_constraint = defaultdict(list)
    for entry in records:
        by_scope[(entry.name, entry.scope)].append(entry)
    for entry in constraints:
        by_constraint[entry.name].append(entry)
    result = []
    for item in records:
        if item.source_kind != "registry":
            result.append(item)
            continue
        peers = [
            other
            for other in by_scope[(item.name, item.scope)]
            if other.name == item.name
            and other.scope == item.scope
            and (other.marker is None or other.marker == item.marker)
        ]
        matching = by_constraint[item.name]
        active = [
            entry
            for entry in matching
            if entry.marker is None or entry.marker == item.marker
        ]
        conditional = [entry for entry in matching if entry not in active]
        specifiers = tuple(
            sorted({entry.specifier for entry in peers + active if entry.specifier})
        )
        origins = tuple(sorted({origin for entry in peers for origin in entry.origins}))
        constraint_origins = tuple(
            sorted({origin for entry in matching for origin in entry.origins})
        )
        base = replace(
            item,
            origins=origins,
            constraint_origins=constraint_origins,
            constraint_specifiers=specifiers,
        )
        if any(entry.source_kind != "registry" for entry in peers + active):
            result.append(
                replace(
                    base, version_state="unsupported", reason="unsupported_constraint"
                )
            )
            continue
        candidates = set()
        unsupported = False
        for text in specifiers:
            for specifier in SpecifierSet(text):
                try:
                    Version(specifier.version.removesuffix(".*"))
                except ValueError:
                    unsupported = True
                else:
                    if (
                        specifier.operator in {"==", "==="}
                        and "*" not in specifier.version
                    ):
                        candidates.add(specifier.version)
        if unsupported:
            result.append(
                replace(base, version_state="unsupported", reason="unsupported_version")
            )
        elif candidates:
            valid = [
                version
                for version in sorted(candidates)
                if all(
                    SpecifierSet(text).contains(version, prereleases=True)
                    for text in specifiers
                )
            ]
            if not valid or len({Version(version) for version in valid}) != 1:
                result.append(
                    replace(base, version_state="conflict", reason="incompatible_pins")
                )
            elif conditional:
                result.append(replace(base, reason="conditional_constraints"))
            else:
                result.append(
                    replace(
                        base,
                        version_state="declared_pin",
                        current_version=valid[0],
                        reason=None,
                    )
                )
        else:
            result.append(
                replace(
                    base,
                    reason="conditional_constraints"
                    if conditional
                    else "no_exact_version",
                )
            )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.name,
                item.scope,
                item.marker or "",
                item.extras,
                item.specifier,
                item.raw_requirement,
            ),
        )
    )
