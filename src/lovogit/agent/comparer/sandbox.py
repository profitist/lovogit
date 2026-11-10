from typing import NotRequired

from typing_extensions import TypedDict

from lovogit.sandbox import TestSandbox


class SandboxState(TypedDict):
    repository_id: str
    repository_sha: str
    repository_path: str
    python_version: str
    dependency_overrides: dict[str, str]
    test_paths: list[str]
    workspace_id: NotRequired[str]
    test_bundle_id: NotRequired[str]
    baseline_environment_id: NotRequired[str]
    candidate_environment_id: NotRequired[str]
    baseline_run_id: NotRequired[str]
    candidate_run_id: NotRequired[str]
    sandbox_summary: NotRequired[dict[str, str | int]]
    verdict: NotRequired[str]


class SandboxNodes:
    """Async callables usable as StateGraph nodes; return partial state updates."""

    def __init__(self, sandbox: TestSandbox):
        self._sandbox = sandbox

    async def prepare_baseline(self, state: SandboxState) -> dict:
        environment = await self._sandbox.prepare_environment(
            state["repository_id"],
            state["repository_sha"],
            state["repository_path"],
            state["python_version"],
        )
        return {
            "baseline_environment_id": environment.environment_id,
            "workspace_id": environment.workspace_id,
        }

    async def freeze_bundle(self, state: SandboxState) -> dict:
        bundle = await self._sandbox.freeze_test_bundle(
            state["workspace_id"], state["test_paths"]
        )
        return {"test_bundle_id": bundle.bundle_id}

    async def prepare_candidate(self, state: SandboxState) -> dict:
        environment = await self._sandbox.prepare_environment(
            state["repository_id"],
            state["repository_sha"],
            None,
            state["python_version"],
            state["dependency_overrides"],
            baseline_environment_id=state["baseline_environment_id"],
        )
        return {"candidate_environment_id": environment.environment_id}

    async def run_baseline(self, state: SandboxState) -> dict:
        return await self._run(state, "baseline")

    async def run_candidate(self, state: SandboxState) -> dict:
        return await self._run(state, "candidate")

    async def compare(self, state: SandboxState) -> dict:
        comparison = await self._sandbox.compare_runs(
            state["baseline_run_id"], state["candidate_run_id"]
        )
        return {
            "verdict": comparison.verdict,
            "sandbox_summary": {
                "explanation": comparison.explanation,
                "introduced_failures": len(comparison.introduced_failures),
            },
        }

    async def _run(self, state: SandboxState, role: str) -> dict:
        environment_id = state[f"{role}_environment_id"]
        bundle_id = state["test_bundle_id"]
        result = await self._sandbox.run_tests(
            environment_id,
            bundle_id,
            idempotency_key=f"{role}:{environment_id}:{bundle_id}",
        )
        return {
            f"{role}_run_id": result.run_id,
            "sandbox_summary": {
                "status": result.status,
                "passed": result.passed,
                "failed": result.failed,
                "errors": result.errors,
            },
        }
