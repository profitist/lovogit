import asyncio
import sys
from contextlib import AsyncExitStack, contextmanager
from datetime import UTC, datetime

from packaging.utils import canonicalize_name

from lovogit.agent.comparer.context import collect_context
from lovogit.agent.update_sources.extractor import UpdateChangeExtractor
from lovogit.app.dependencies.inventory import read_dependency_inventory
from lovogit.app.project_update.code_evidence import verify_location
from lovogit.app.project_update.dto import (
    ChangeSearchDTO,
    ProjectUpdateResultDTO,
    StageResultDTO,
    analysis_status,
)
from lovogit.app.project_update.report import save_report
from lovogit.app.project_update.tracing import RunTrace
from lovogit.app.providers.source_documents.loader import SourceDocumentLoader
from lovogit.app.update_selection import select_latest_stable_update
from lovogit.app.update_sources.analysis import analyze_sources
from lovogit.app.update_sources.collection import collect_sources
from lovogit.app.update_sources.fetching import fetch_sources
from lovogit.cfg.project_update import ProjectUpdateSettings
from lovogit.cfg.source_analysis import SourceAnalysisSettings
from lovogit.cfg.source_fetch import SourceFetchSettings
from lovogit.index.worker import open_index, run_index_operation


@contextmanager
def _stage(result, folder, trace, name):
    result.stages[name] = StageResultDTO("running", "")
    save_report(result, folder)
    try:
        with trace.observe(name) as observation:
            yield
            observation["status"] = "completed"
        result.stages[name] = StageResultDTO("completed", "")
    except BaseException as error:
        reason = (
            "cancelled"
            if isinstance(error, asyncio.CancelledError)
            else type(error).__name__
        )
        result.stages[name] = StageResultDTO("failed", reason)
        raise
    finally:
        pending = sys.exception()
        try:
            save_report(result, folder)
        except OSError as error:
            if pending:
                pending.add_note(f"Report storage also failed: {type(error).__name__}")
            else:
                raise


async def analyze_project_update(
    request,
    *,
    folder,
    repos,
    pypi,
    model_factory,
    loader_factory=None,
    limits=None,
    analysis_settings=None,
    trace=None,
):
    limits = limits or ProjectUpdateSettings()
    analysis_settings = analysis_settings or SourceAnalysisSettings()
    trace = trace or RunTrace(folder, limits)
    result = ProjectUpdateResultDTO(folder.name, request)
    save_report(result, folder)
    try:
        async with asyncio.timeout(limits.UPDATE_DEADLINE), AsyncExitStack() as stack:
            with _stage(result, folder, trace, "snapshot"):
                snapshot = await stack.enter_async_context(
                    repos.open_source(request.repo_url)
                )
                result.repo = snapshot.repo
            with _stage(result, folder, trace, "inventory"):
                inventory = await run_index_operation(
                    None, read_dependency_inventory, snapshot, source=request.source
                )
                result.inventory = inventory
            if inventory.status in {"failed", "no_sources"}:
                result.reason = (
                    "no_sources"
                    if inventory.status == "no_sources"
                    else "inventory_failed"
                )
                return result
            matches = [
                item
                for item in inventory.dependencies
                if item.name == canonicalize_name(request.package, validate=True)
            ]
            if len(matches) != 1:
                result.reason = (
                    "ambiguous_dependency" if matches else "dependency_not_found"
                )
                return result
            with _stage(result, folder, trace, "selection"):
                result.selection = await select_latest_stable_update(matches[0], pypi)
            if result.selection.status != "selected":
                return result
            pair = result.selection.request
            with _stage(result, folder, trace, "collection"):
                result.collection = await collect_sources(pair, pypi)
            with _stage(result, folder, trace, "fetching"):
                loader = (
                    loader_factory()
                    if loader_factory
                    else SourceDocumentLoader(SourceFetchSettings())
                )
                result.fetching = await fetch_sources(result.collection, loader)
            with _stage(result, folder, trace, "extraction"):
                has_text = any(
                    item.status == "fetched" and item.text and item.text.strip()
                    for item in result.fetching.results
                )
                model = model_factory("extraction") if has_text else None
                extractor = UpdateChangeExtractor(model, analysis_settings)
                result.extraction = await analyze_sources(
                    result.fetching, extractor, analysis_settings
                )
            extraction = result.extraction
            result.limitations.extend(
                item
                for item in extraction.limitations
                if item not in result.limitations
            )
            result.searches = [
                ChangeSearchDTO(f"change-{index}", reason="change_limit")
                for index in range(len(extraction.changes))
            ]
            if not extraction.changes:
                return result
            with _stage(result, folder, trace, "index"):
                toolkit, executor = await stack.enter_async_context(
                    open_index(snapshot.root, folder / "code-index.sqlite")
                )
            with _stage(result, folder, trace, "compare"):
                for index, (change, search) in enumerate(
                    zip(extraction.changes, result.searches)
                ):
                    if index >= limits.UPDATE_MAX_CHANGES:
                        break
                    search.outcome, search.reason = "running", None
                    try:
                        trace.check_budget()
                        with trace.observe(search.change_id):
                            model = model_factory("compare")
                            response = await collect_context(
                                {
                                    "repository_id": str(snapshot.repo.repo_id),
                                    "repository_sha": snapshot.repo.commit_sha,
                                    "change": {
                                        "package": pair.package,
                                        "current_version": pair.current_version,
                                        "target_version": pair.target_version,
                                        "changed_api": change.symbol or "",
                                        "change_description": f"{change.category}: {change.description}",
                                    },
                                },
                                model=model,
                                toolkit=toolkit,
                                executor=executor,
                                model_call_limit=limits.UPDATE_MODEL_CALLS,
                                tool_call_limit=limits.UPDATE_TOOL_CALLS,
                                config={
                                    "recursion_limit": limits.UPDATE_MODEL_CALLS * 4
                                    + 8,
                                    "callbacks": model.callbacks,
                                },
                                timeout=limits.UPDATE_COMPARE_TIMEOUT,
                            )
                        search.context = response["context"]
                        search.locations = [
                            verify_location(item, snapshot, limits)
                            for item in search.context["usages"]
                        ]
                        search.related_tests = [
                            verify_location({"file": path, "line": 1}, snapshot, limits)
                            for path in search.context["related_tests"]
                        ]
                        verified = all(
                            item["verified"]
                            for item in search.locations + search.related_tests
                        )
                        search.outcome = (
                            "completed"
                            if search.context["analysis_complete"] and verified
                            else "partial"
                        )
                        if search.outcome == "partial":
                            search.reason = "incomplete_or_unverified_search"
                    except Exception as error:  # noqa: BLE001
                        search.outcome, search.reason = "failed", type(error).__name__
                    save_report(result, folder)
    except asyncio.CancelledError:
        result.reason = "cancelled"
        raise
    except Exception as error:  # noqa: BLE001
        result.reason = type(error).__name__
        result.diagnostics.append(type(error).__name__)
        for attribute in (
            "reason",
            "retry_after",
            "package",
            "version",
            "checked_versions",
        ):
            value = getattr(error, attribute, None)
            if value is not None:
                result.diagnostics.append(f"{attribute}: {value}")
    finally:
        for search in result.searches:
            if search.outcome == "running":
                search.outcome, search.reason = "failed", result.reason or "interrupted"
        result.status = analysis_status(result)
        result.ended_at = datetime.now(UTC).isoformat()
        pending = sys.exception()
        try:
            save_report(result, folder)
        except OSError as error:
            if pending:
                pending.add_note(f"Report storage also failed: {type(error).__name__}")
            else:
                if result.reason:
                    error.add_note(f"Original analysis failure: {result.reason}")
                raise
    return result
