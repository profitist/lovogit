import asyncio
import math
from dataclasses import dataclass
from typing import Literal

from packaging.utils import canonicalize_name
from packaging.version import Version

from lovogit.app.dependencies.dto import DependencyDeclarationDTO
from lovogit.app.providers.pypi.client import PyPiProvider
from lovogit.app.providers.pypi.dto import PackageInfoDTO
from lovogit.app.providers.pypi.errors import InvalidPyPiResponse
from lovogit.app.update_sources.dto import UpdateSourceRequest


@dataclass(frozen=True)
class ExcludedReleaseDTO:
    version: str
    reason: str
    yanked_reason: str | None = None


@dataclass(frozen=True)
class UpdateSelectionDTO:
    dependency: DependencyDeclarationDTO
    status: Literal["selected", "no_update", "skipped"]
    reason: str | None = None
    policy: Literal["latest_stable"] = "latest_stable"
    checked_versions: tuple[str, ...] = ()
    excluded_versions: tuple[ExcludedReleaseDTO, ...] = ()
    target_metadata: PackageInfoDTO | None = None
    request: UpdateSourceRequest | None = None


class UpdateSelectionError(Exception):
    def __init__(self, package: str, reason: str, checked_versions: tuple[str, ...]):
        self.package = package
        self.reason = reason
        self.checked_versions = checked_versions
        super().__init__(
            f"Cannot select latest stable update for {package}: {reason} "
            f"(checked={len(checked_versions)})"
        )


async def select_latest_stable_update(
    dependency: DependencyDeclarationDTO,
    provider: PyPiProvider,
    *,
    max_release_checks: int = 32,
    timeout: float = 120,
) -> UpdateSelectionDTO:
    if type(max_release_checks) is not int or max_release_checks <= 0:
        raise ValueError(
            "Update selection requires a positive integer max_release_checks"
        )
    try:
        valid_timeout = (
            type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
        )
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise ValueError("Update selection requires a positive finite timeout")
    reason = None
    if dependency.source_kind != "registry":
        reason = "unsupported_source"
    elif dependency.version_state == "conflict":
        reason = "conflicting_current"
    elif dependency.version_state == "unsupported":
        reason = "unsupported_current"
    elif (
        dependency.version_state not in {"declared_pin", "locked"}
        or not dependency.current_version
    ):
        reason = "unknown_current"
    else:
        try:
            current = Version(dependency.current_version)
        except ValueError, TypeError:
            reason = "invalid_current"
    if reason:
        return UpdateSelectionDTO(dependency, "skipped", reason)
    try:
        name = canonicalize_name(dependency.name, validate=True)
    except ValueError, TypeError:
        raise ValueError(
            f"Cannot select update for invalid package name {dependency.name!r}"
        ) from None
    checked = []
    excluded = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    operation_timeout = asyncio.timeout_at(deadline)
    try:
        async with operation_timeout:
            index = await provider.get_package(name)
            try:
                valid_name = canonicalize_name(index.name, validate=True) == name
            except ValueError, TypeError:
                valid_name = False
            if not valid_name:
                raise InvalidPyPiResponse(name, None, "package index name mismatch")
            candidates, initial_exclusions = _newer_stable_versions(
                index.versions, current
            )
            excluded.extend(initial_exclusions)
            if loop.time() >= deadline:
                raise UpdateSelectionError(name, "deadline_exceeded", tuple(checked))
            for version, raw in candidates:
                if len(checked) >= max_release_checks:
                    raise UpdateSelectionError(
                        name, "release_check_limit", tuple(checked)
                    )
                metadata = await provider.get_package_info(name, raw)
                try:
                    valid_identity = (
                        canonicalize_name(metadata.name, validate=True) == name
                        and Version(metadata.version) == version
                    )
                except ValueError, TypeError:
                    valid_identity = False
                if not valid_identity:
                    raise InvalidPyPiResponse(name, raw, "release identity mismatch")
                checked.append(raw)
                if loop.time() >= deadline:
                    raise UpdateSelectionError(
                        name, "deadline_exceeded", tuple(checked)
                    )
                if metadata.yanked:
                    excluded.append(
                        ExcludedReleaseDTO(raw, "yanked", metadata.yanked_reason)
                    )
                    continue
                return UpdateSelectionDTO(
                    dependency,
                    "selected",
                    checked_versions=tuple(checked),
                    excluded_versions=tuple(excluded),
                    target_metadata=metadata,
                    request=UpdateSourceRequest(name, dependency.current_version, raw),
                )
    except TimeoutError:
        if not operation_timeout.expired():
            raise
        raise UpdateSelectionError(name, "deadline_exceeded", tuple(checked)) from None
    return UpdateSelectionDTO(
        dependency,
        "no_update",
        "no_newer_stable",
        checked_versions=tuple(checked),
        excluded_versions=tuple(excluded),
    )


def _newer_stable_versions(versions: tuple[str, ...], current: Version):
    groups: dict[Version, set[str]] = {}
    excluded = []
    for raw in sorted(set(versions)):
        try:
            version = Version(raw)
        except ValueError:
            excluded.append(ExcludedReleaseDTO(raw, "invalid_version"))
            continue
        reason = None
        if version.is_devrelease:
            reason = "dev_release"
        elif version.is_prerelease:
            reason = "prerelease"
        elif version.local is not None:
            reason = "local_version"
        if reason:
            excluded.append(ExcludedReleaseDTO(raw, reason))
        elif version > current:
            groups.setdefault(version, set()).add(raw)
    candidates = []
    for version in sorted(groups, reverse=True):
        originals = groups[version]
        representative = str(version) if str(version) in originals else min(originals)
        candidates.append((version, representative))
        excluded.extend(
            ExcludedReleaseDTO(raw, "equivalent_version")
            for raw in originals
            if raw != representative
        )
    return tuple(candidates), tuple(
        sorted(excluded, key=lambda item: (item.version, item.reason))
    )
