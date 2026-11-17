from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError

from lovogit.agent.update_sources.schemas import ChangeSchema, EvidenceSchema
from lovogit.app.update_sources.dto import (
    ChangeEvidenceDTO,
    FetchedSourceDTO,
    UpdateChangeDTO,
    UpdateSourcesDTO,
)


def validate_release_pair(sources: UpdateSourcesDTO) -> tuple[Version, Version, str]:
    request = sources.request
    try:
        current = Version(request.current_version)
        target = Version(request.target_version)
        package = canonicalize_name(request.package, validate=True)
        if current >= target:
            raise ValueError("current version must precede target version")
        for source, expected in ((sources.current, current), (sources.target, target)):
            if (
                canonicalize_name(source.metadata.name, validate=True) != package
                or canonicalize_name(source.origin.package, validate=True) != package
            ):
                raise ValueError("release package does not match request")
            if (
                Version(source.metadata.version) != expected
                or Version(source.origin.version) != expected
                or Version(source.origin.requested_version) != expected
            ):
                raise ValueError("release version does not match request")
    except (InvalidVersion, ValueError) as error:
        raise ValueError(
            f"Cannot analyze update sources for {request.package!r}: {error}"
        ) from error
    return current, target, package


def validate_change(
    raw, source: FetchedSourceDTO, *, source_id: str, current: Version, target: Version
) -> UpdateChangeDTO:
    try:
        item = ChangeSchema.model_validate(raw)
    except ValidationError:
        raise ValueError("invalid_record") from None
    evidence = tuple(
        _validate_evidence(entry, source_id, source) for entry in item.evidence
    )
    if item.symbol is not None and (
        not item.symbol.strip()
        or not any(item.symbol in entry.quote for entry in evidence)
    ):
        raise ValueError("unsupported_symbol")
    version_evidence = None
    version = None
    if item.release_version is not None and item.version_evidence is not None:
        try:
            version_evidence = _validate_evidence(
                item.version_evidence, source_id, source
            )
            if item.release_version not in version_evidence.quote:
                raise ValueError("unknown_version")
            version = Version(item.release_version)
        except ValueError:
            version_evidence = None
    if version is not None and (not current < version <= target):
        raise ValueError("outside_interval")
    return UpdateChangeDTO(
        item.category,
        item.description,
        item.symbol,
        item.release_version,
        evidence,
        version_evidence,
        "unknown_version" if version is None else None,
    )


def _validate_evidence(entry: EvidenceSchema, source_id, source) -> ChangeEvidenceDTO:
    if entry.source_id != source_id:
        raise ValueError("unknown_source")
    if not entry.quote.strip() or entry.quote not in source.text:
        raise ValueError("invalid_quote")
    start = source.text.index(entry.quote)
    return ChangeEvidenceDTO(
        source_id,
        entry.quote,
        start,
        start + len(entry.quote),
        source.links,
        source.final_url,
    )
