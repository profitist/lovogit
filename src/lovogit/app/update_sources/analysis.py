import asyncio

from packaging.version import Version

from lovogit.agent.update_sources.extractor import SourceAnalysisError
from lovogit.app.update_sources.dto import (
    DocumentAnalysisDTO,
    RejectedChangeDTO,
    UpdateSourceAnalysisResultDTO,
    UpdateSourceFetchResultDTO,
)
from lovogit.app.update_sources.validation import validate_change, validate_release_pair
from lovogit.cfg.source_analysis import SourceAnalysisSettings


async def analyze_sources(
    fetched: UpdateSourceFetchResultDTO,
    extractor,
    settings: SourceAnalysisSettings | None = None,
) -> UpdateSourceAnalysisResultDTO:
    settings = settings or SourceAnalysisSettings()
    current, target, package = validate_release_pair(fetched.sources)
    documents = []
    changes, unscoped, rejected = [], [], []
    limitations = [
        "coverage_undetermined",
        "quotes_do_not_prove_interpretation",
        "empty_changes_do_not_prove_compatibility",
    ]
    deadline = asyncio.get_running_loop().time() + settings.SOURCE_ANALYSIS_DEADLINE
    total_chars = calls = candidates = 0
    for index, source in enumerate(fetched.results):
        source_id = f"source-{index}"
        if source.status != "fetched" or not source.text or not source.text.strip():
            documents.append(
                DocumentAnalysisDTO(source_id, "skipped", source.reason or "no_text")
            )
            limitations.append("unavailable_materials")
            continue
        candidates += 1
        reason = None
        if asyncio.get_running_loop().time() >= deadline:
            reason = "deadline_exceeded"
        elif calls >= settings.SOURCE_ANALYSIS_MAX_DOCUMENTS:
            reason = "document_limit"
        elif len(source.text) > settings.SOURCE_ANALYSIS_MAX_DOCUMENT_CHARS:
            reason = "document_size_limit"
        elif total_chars + len(source.text) > settings.SOURCE_ANALYSIS_MAX_TOTAL_CHARS:
            reason = "total_size_limit"
        if reason:
            documents.append(DocumentAnalysisDTO(source_id, "skipped", reason))
            continue
        calls += 1
        total_chars += len(source.text)
        try:
            async with asyncio.timeout_at(deadline):
                response = await extractor.extract(
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
                change = validate_change(
                    raw, source, source_id=source_id, current=current, target=target
                )
            except ValueError as error:
                rejected.append(RejectedChangeDTO(source_id, change_index, str(error)))
                continue
            if change.reason == "unknown_version":
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
