import json

from .models import TestFailure as TestFailureDTO
from .models import TestRunResult as TestRunResultDTO
from .storage import ArtifactStore, SandboxError


def parse_report(
    result: TestRunResultDTO, state: dict, files: dict[str, bytes], store: ArtifactStore
) -> TestRunResultDTO:
    updates = {"exit_code": state.get("ExitCode")}
    for name, field in (
        ("report.json", "report_artifact_id"),
        ("coverage.json", "coverage_artifact_id"),
    ):
        if name in files:
            updates[field] = store.artifact(files[name])
    code = state.get("ExitCode")
    if state.get("TimedOut"):
        updates["status"] = "timeout"
    elif state.get("OOMKilled"):
        updates["status"] = "resource_limit"
    elif code not in {0, 1, 2, 3, 4, 5} or code in {3, 4}:
        updates["status"] = "infrastructure_error"
    elif code == 5:
        updates["status"] = "no_tests"
    else:
        updates["status"] = {0: "passed", 1: "failed", 2: "collection_error"}[code]
    if "report.json" not in files:
        if updates["status"] in {"passed", "failed", "collection_error"}:
            updates.update(
                status="infrastructure_error", reason="pytest JSON report is missing"
            )
        return result.model_copy(update=updates)
    try:
        report = json.loads(files["report.json"])
        tests = report.get("tests", [])
        if not isinstance(tests, list) or len(tests) > 20000:
            raise ValueError("invalid test list")
        outcomes = {}
        phases = {}
        failures = []
        for test in tests:
            node = test["nodeid"]
            if node in outcomes:
                raise ValueError("duplicate test node")
            outcomes[node] = test["outcome"]
            phases[node] = {
                phase: test[phase]["outcome"]
                for phase in ("setup", "call", "teardown")
                if phase in test
            }
            for phase in ("setup", "call", "teardown"):
                detail = test.get(phase, {})
                if detail.get("outcome") != "failed" or len(failures) >= 20:
                    continue
                traceback = str(detail.get("longrepr", ""))
                crash = detail.get("crash", {})
                message = str(
                    crash.get(
                        "message",
                        traceback.splitlines()[-1] if traceback else "pytest failure",
                    )
                )
                failures.append(
                    TestFailureDTO(
                        node_id=node,
                        phase=phase,
                        error_type=message.split(":", 1)[0][:120],
                        message=message[:500],
                        traceback=traceback[-2000:],
                        traceback_artifact_id=store.artifact(traceback.encode()),
                        duration_seconds=detail.get("duration", 0),
                    )
                )
        collectors = [
            entry
            for entry in report.get("collectors", [])
            if entry.get("outcome") == "failed"
        ]
        for collector in collectors[: max(0, 20 - len(failures))]:
            traceback = str(collector.get("longrepr", ""))
            failures.append(
                TestFailureDTO(
                    node_id=collector.get("nodeid", ""),
                    phase="collect",
                    error_type="CollectionError",
                    message=traceback[-500:],
                    traceback=traceback[-2000:],
                    traceback_artifact_id=store.artifact(traceback.encode()),
                    duration_seconds=0,
                )
            )
        summary = report.get("summary", {})
        updates.update(
            passed=summary.get("passed", 0),
            failed=summary.get("failed", 0),
            skipped=summary.get("skipped", 0),
            errors=max(summary.get("error", 0), len(collectors)),
            outcomes=outcomes,
            phases=phases,
            failures=failures,
        )
        if collectors and updates["status"] in {"failed", "passed"}:
            updates["status"] = "collection_error"
        if updates["status"] == "passed" and (
            not outcomes
            or any(
                value not in {"passed", "skipped", "xfailed"}
                for value in outcomes.values()
            )
        ):
            updates.update(
                status="infrastructure_error",
                reason="pytest exit status contradicts report",
            )
        return TestRunResultDTO.model_validate(
            result.model_copy(update=updates).model_dump()
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SandboxError(f"Malformed pytest report for run {result.run_id}") from exc
