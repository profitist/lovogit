import asyncio

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError

from lovogit.agent.update_sources.extractor import SourceAnalysisError
from lovogit.agent.update_sources.schemas import ChangeSchema, EvidenceSchema
from lovogit.app.services.update_source_analysis_dto import (
    ChangeEvidenceDTO,
    DocumentAnalysisDTO,
    RejectedChangeDTO,
    UpdateChangeDTO,
    UpdateSourceAnalysisResultDTO,
)
from lovogit.app.services.update_source_fetch_dto import UpdateSourceFetchResultDTO
from lovogit.cfg.source_analysis import SourceAnalysisSettings


class UpdateSourceAnalysisService:
    def __init__(self, extractor, settings: SourceAnalysisSettings | None = None):
        self._extractor = extractor
        self._settings = settings or SourceAnalysisSettings()

    async def analyze(
        self, fetched: UpdateSourceFetchResultDTO
    ) -> UpdateSourceAnalysisResultDTO:
        request = fetched.sources.request
        try:
            current = Version(request.current_version)
            target = Version(request.target_version)
            package = canonicalize_name(request.package, validate=True)
            if current >= target:
                raise ValueError("current version must precede target version")
            for source, expected in (
                (fetched.sources.current, current),
                (fetched.sources.target, target),
            ):
                if (
                    canonicalize_name(source.metadata.name, validate=True) != package
                    or canonicalize_name(source.origin.package, validate=True)
                    != package
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
        documents = []
        changes, unscoped, rejected = [], [], []
        limitations = [
            "coverage_undetermined",
            "quotes_do_not_prove_interpretation",
            "empty_changes_do_not_prove_compatibility",
        ]
        deadline = (
            asyncio.get_running_loop().time() + self._settings.SOURCE_ANALYSIS_DEADLINE
        )
        total_chars = calls = candidates = 0
        for index, source in enumerate(fetched.results):
            source_id = f"source-{index}"
            if source.status != "fetched" or not source.text or not source.text.strip():
                documents.append(
                    DocumentAnalysisDTO(
                        source_id, "skipped", source.reason or "no_text"
                    )
                )
                limitations.append("unavailable_materials")
                continue
            candidates += 1
            reason = None
            if asyncio.get_running_loop().time() >= deadline:
                reason = "deadline_exceeded"
            elif calls >= self._settings.SOURCE_ANALYSIS_MAX_DOCUMENTS:
                reason = "document_limit"
            elif len(source.text) > self._settings.SOURCE_ANALYSIS_MAX_DOCUMENT_CHARS:
                reason = "document_size_limit"
            elif (
                total_chars + len(source.text)
                > self._settings.SOURCE_ANALYSIS_MAX_TOTAL_CHARS
            ):
                reason = "total_size_limit"
            if reason:
                documents.append(DocumentAnalysisDTO(source_id, "skipped", reason))
                continue
            calls += 1
            total_chars += len(source.text)
            try:
                async with asyncio.timeout_at(deadline):
                    response = await self._extractor.extract(
                        {
                            "source_id": source_id,
                            "package": package,
                            "current_version": str(current),
                            "target_version": str(target),
                            "content_type": source.content_type,
                            "text": source.text,
                        }
                    )
            except TimeoutError:
                documents.append(DocumentAnalysisDTO(source_id, "failed", "timeout"))
                continue
            except SourceAnalysisError as error:
                documents.append(DocumentAnalysisDTO(source_id, "failed", error.reason))
                continue
            documents.append(DocumentAnalysisDTO(source_id, "processed"))
            limitations.extend(response.limitations)
            for change_index, raw in enumerate(response.changes):
                try:
                    item = ChangeSchema.model_validate(raw)
                except ValidationError:
                    rejected.append(
                        RejectedChangeDTO(source_id, change_index, "invalid_record")
                    )
                    continue
                try:
                    evidence = tuple(
                        _validate_evidence(entry, source_id, source)
                        for entry in item.evidence
                    )
                    if item.symbol is not None and (
                        not item.symbol.strip()
                        or not any(item.symbol in entry.quote for entry in evidence)
                    ):
                        raise ValueError("unsupported_symbol")
                except ValueError as error:
                    rejected.append(
                        RejectedChangeDTO(source_id, change_index, str(error))
                    )
                    continue
                version_evidence = None
                version = None
                if (
                    item.release_version is not None
                    and item.version_evidence is not None
                ):
                    try:
                        version_evidence = _validate_evidence(
                            item.version_evidence, source_id, source
                        )
                        if item.release_version not in version_evidence.quote:
                            raise ValueError("unknown_version")
                        version = Version(item.release_version)
                    except ValueError:
                        version_evidence = None
                if version is not None and not current < version <= target:
                    rejected.append(
                        RejectedChangeDTO(source_id, change_index, "outside_interval")
                    )
                    continue
                change = UpdateChangeDTO(
                    item.category,
                    item.description,
                    item.symbol,
                    item.release_version,
                    evidence,
                    version_evidence,
                    "unknown_version" if version is None else None,
                )
                if version is None:
                    unscoped.append(change)
                    limitations.append("unknown_version")
                else:
                    changes.append(change)
        processed = sum(item.status == "processed" for item in documents)
        if candidates == 0:
            status = "no_materials"
        elif processed == 0 and calls:
            status = "failed"
        elif (
            processed == 0
            or any(item.status != "processed" for item in documents)
            or rejected
            or unscoped
        ):
            status = "partial"
        else:
            status = "completed"
        return UpdateSourceAnalysisResultDTO(
            fetched,
            status,
            tuple(documents),
            tuple(changes),
            tuple(unscoped),
            tuple(rejected),
            tuple(sorted({item.release_version for item in changes}, key=Version)),
            tuple(dict.fromkeys(limitations)),
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
