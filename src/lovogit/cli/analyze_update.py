import argparse
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
from pydantic import ValidationError

from lovogit.app.project_update.artifacts import create_run_directory
from lovogit.app.project_update.dto import ProjectUpdateRequest, ProjectUpdateResultDTO
from lovogit.app.project_update.report import save_report
from lovogit.app.project_update.trace_export import export_trace
from lovogit.app.project_update.tracing import RunTrace, TraceCallbacks
from lovogit.app.project_update.workflow import analyze_project_update
from lovogit.app.providers.github.client import GitHubProvider
from lovogit.app.providers.llm.client import (
    UpdateModelConfigurationError,
    create_update_model,
)
from lovogit.app.providers.pypi.client import PyPiProvider
from lovogit.app.services.repo import RepoService
from lovogit.cfg.github import GitHubSettings
from lovogit.cfg.project_update import (
    ProjectUpdateSettings,
    TraceExportSettings,
    UpdateModelSettings,
)
from lovogit.cfg.pypi import PyPiSettings


async def execute(request, folder, *, langfuse=False):
    result = ProjectUpdateResultDTO(folder.name, request)
    save_report(result, folder)
    try:
        limits = ProjectUpdateSettings()
        github_settings = GitHubSettings()
        trace = RunTrace(
            folder, limits, secrets=(github_settings.GITHUB_TOKEN.get_secret_value(),)
        )
        callback = TraceCallbacks(trace)
        async with AsyncExitStack() as stack:
            github_client = await stack.enter_async_context(
                httpx.AsyncClient(trust_env=False)
            )
            pypi_client = await stack.enter_async_context(
                httpx.AsyncClient(trust_env=False)
            )
            model_client = await stack.enter_async_context(
                httpx.AsyncClient(trust_env=False)
            )
            models = {}

            def model_factory(role):
                if role not in models:
                    try:
                        settings = UpdateModelSettings()
                    except ValidationError as error:
                        fields = [
                            str(item["loc"][0])
                            for item in error.errors(include_input=False)
                        ]
                        raise UpdateModelConfigurationError(fields) from None
                    trace.protect(settings.LLM_API_KEY.get_secret_value())
                    models[role] = create_update_model(
                        settings,
                        limits,
                        role=role,
                        client=model_client,
                        callbacks=[callback],
                    )
                return models[role]

            repos = RepoService(GitHubProvider(github_client, github_settings))
            pypi = PyPiProvider(pypi_client, PyPiSettings())
            result = await analyze_project_update(
                request,
                folder=folder,
                repos=repos,
                pypi=pypi,
                model_factory=model_factory,
                limits=limits,
                trace=trace,
            )
        if langfuse:
            try:
                result.export = await export_trace(folder, TraceExportSettings())
            except (OSError, ValueError, KeyError, TypeError) as error:
                result.export = {"status": "failed", "reason": type(error).__name__}
            save_report(result, folder)
    except Exception as error:  # noqa: BLE001
        result.reason = type(error).__name__
        if isinstance(error, ValidationError):
            result.diagnostics.extend(
                str(item["loc"][0]) for item in error.errors(include_input=False)
            )
        save_report(result, folder)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Analyze one project dependency update without modifying the project"
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument(
        "--source", choices=("auto", "pyproject", "requirements"), default="auto"
    )
    parser.add_argument(
        "--langfuse",
        action="store_true",
        help="Upload saved model/code traces to Langfuse",
    )
    args = parser.parse_args(argv)
    folder = None
    try:
        folder = create_run_directory(Path.cwd(), args.package)
        request = ProjectUpdateRequest(args.repo, args.package, args.source)
        result = asyncio.run(execute(request, folder, langfuse=args.langfuse))
    except KeyboardInterrupt:
        print(f"Analysis interrupted; available report: {folder}")
        return 130
    except (OSError, ValueError) as error:
        print(
            f"Analysis could not save artifacts: {type(error).__name__}; directory: {folder}"
        )
        return 1
    print(f"Analysis: {result.status}; reason: {result.reason or '-'}")
    for diagnostic in result.diagnostics:
        print(diagnostic)
    print(f"Report: {folder / 'report.html'}")
    print(f"Trace: {folder / 'trace.json'}")
    if result.export:
        print(f"Langfuse export: {result.export['status']}")
    if result.export and result.export["status"] != "accepted":
        return 1
    return {"completed": 0, "no_update": 0, "skipped": 0, "partial": 2, "failed": 1}[
        result.status
    ]


if __name__ == "__main__":
    raise SystemExit(main())
