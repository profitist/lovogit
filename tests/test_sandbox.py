import asyncio
import json
import tarfile
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from lovogit.cfg.sandbox import SandboxLimits, SandboxSettings
from lovogit.sandbox import SandboxError, TestSandbox
from lovogit.sandbox.docker import DockerRuntime
from lovogit.sandbox.models import TestRunResult as RunDTO
from lovogit.sandbox.reports import parse_report
from lovogit.sandbox.storage import ArtifactStore, safe_path, snapshot_repository

SHA = "a" * 40


def report(outcome="passed", phase="call", code=0):
    detail = {"outcome": outcome, "duration": 0.1}
    if outcome == "failed":
        detail.update(
            longrepr="trace\nValueError: changed",
            crash={"message": "ValueError: changed"},
        )
    return {
        "summary": {
            "passed": int(outcome == "passed"),
            "failed": int(outcome == "failed"),
            "skipped": int(outcome == "skipped"),
        },
        "tests": [
            {"nodeid": "test_api.py::test_api", "outcome": outcome, phase: detail}
        ],
        "collectors": [],
        "exitcode": code,
    }


class SandboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "requirements.txt").write_text("idna>=3.10,<4\n")
        (self.repo / "test_existing.py").write_text(
            "def test_existing(): assert True\n"
        )
        self.settings = SandboxSettings(
            root=self.root / "store", image="test-image", _env_file=None
        )
        self.sandbox = TestSandbox(self.settings)
        self.runtime = AsyncMock(spec=DockerRuntime)
        self.runtime.image_info.return_value = ("sha256:example", "linux/arm64")
        self.runtime.execute.side_effect = self.execute
        self.execution_report = report()
        self.execution_state = {"ExitCode": 0}
        self.last_request = None
        self.patch = patch.object(self.sandbox, "_runtime", self.runtime)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    async def execute(self, identifier, image, mode, request, **kwargs):
        self.last_request = request
        if mode == "prepare":
            version = request["dependency_overrides"].get("idna", "3.10")
            if version == "9.9":
                return {"ExitCode": 1}, b"resolution failed", {}
            metadata = {
                "python_version": "3.12.10",
                "installed_dependencies": {"idna": version, "pytest": "8.3.5"},
            }
            return (
                {"ExitCode": 0},
                b"prepared",
                {
                    "preparation.json": json.dumps(metadata).encode(),
                    "resolved.lock": b"idna==3.10\n",
                },
            )
        coverage = {
            "totals": {"percent_covered": 75},
            "files": {
                "/workspace/repository/api.py": {
                    "executed_lines": [1, 2],
                    "missing_lines": [3],
                    "executed_branches": [[1, 2]],
                    "summary": {"percent_covered": 75},
                }
            },
        }
        return (
            self.execution_state,
            b"stdout\nstderr",
            {
                "report.json": json.dumps(self.execution_report).encode(),
                "coverage.json": json.dumps(coverage).encode(),
            },
        )

    async def prepare(self, overrides=None, **kwargs):
        return await self.sandbox.prepare_environment(
            "repo", SHA, str(self.repo), "3.12", overrides, **kwargs
        )

    async def bundle(self, env):
        await self.sandbox.write_test(
            env.workspace_id, "test_api.py", "def test_api(): assert True\n"
        )
        return await self.sandbox.freeze_test_bundle(
            env.workspace_id, ["generated/test_api.py"]
        )

    async def test_baseline_versions_snapshot_and_idempotency(self):
        (self.repo / ".env").write_text("secret")
        env = await self.prepare()
        self.assertEqual(env.status, "ready")
        self.assertEqual(env.installed_dependencies["idna"], "3.10")
        self.assertEqual(env, await self.prepare())
        self.assertEqual(self.runtime.execute.await_count, 1)
        snapshot = self.settings.root / "snapshots" / env.environment_id
        self.assertFalse((snapshot / ".env").exists())
        self.assertEqual(
            (self.repo / "requirements.txt").read_text(), "idna>=3.10,<4\n"
        )

    async def test_candidate_separate_and_transitive_changes(self):
        baseline = await self.prepare()
        candidate = await self.prepare({"idna": "3.11"})
        self.assertEqual(candidate.status, "ready")
        self.assertNotEqual(baseline.environment_id, candidate.environment_id)
        self.assertEqual(candidate.dependency_changes, {"idna": ("3.10", "3.11")})
        self.assertEqual(baseline.installed_dependencies["idna"], "3.10")

    async def test_incompatible_version_blocked(self):
        candidate = await self.prepare({"idna": "9.9"})
        self.assertEqual(candidate.status, "blocked")
        self.assertIn("preparation failed", candidate.reason)

    async def test_docker_unavailable_returns_blocked(self):
        self.runtime.image_info.side_effect = SandboxError("daemon unavailable")
        self.runtime.cleanup.side_effect = SandboxError("daemon unavailable")
        self.assertEqual((await self.prepare()).status, "blocked")

    async def test_bundle_immutable_and_shared(self):
        baseline = await self.prepare()
        bundle = await self.bundle(baseline)
        await self.sandbox.write_test(
            baseline.workspace_id, "test_api.py", "def test_api(): assert False\n"
        )
        updated = await self.sandbox.freeze_test_bundle(
            baseline.workspace_id, ["generated/test_api.py"]
        )
        self.assertNotEqual(bundle.fingerprint, updated.fingerprint)
        self.assertEqual(
            (
                await self.sandbox.run_tests(baseline.environment_id, bundle.bundle_id)
            ).status,
            "passed",
        )
        candidate = await self.prepare({"idna": "3.11"})
        run = await self.sandbox.run_tests(candidate.environment_id, bundle.bundle_id)
        self.assertEqual(run.test_bundle_id, bundle.bundle_id)

    async def test_existing_tests_and_node_ids(self):
        env = await self.prepare()
        bundle = await self.sandbox.freeze_test_bundle(
            env.workspace_id, ["repository/test_existing.py::test_existing"]
        )
        await self.sandbox.run_tests(env.environment_id, bundle.bundle_id)
        self.assertEqual(
            self.last_request["test_paths"],
            ["repository/test_existing.py::test_existing"],
        )

    async def test_bundle_tampering_rejected(self):
        env = await self.prepare()
        bundle = await self.bundle(env)
        path = self.settings.root / "bundle_files" / bundle.bundle_id / "test_api.py"
        path.chmod(0o644)
        path.write_text("changed")
        with self.assertRaises(SandboxError):
            await self.sandbox.run_tests(env.environment_id, bundle.bundle_id)

    async def test_paths_and_arguments_rejected(self):
        env = await self.prepare()
        for path in (
            "../outside.py",
            "/etc/passwd",
            "x/../../a.py",
            "a\\b.py",
            "-x.py",
            "a.py\n-x",
            "a.py::bad",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.sandbox.write_test(env.workspace_id, path, "test")
        for path in ("repository/../outside.py", "--collect-only", "/etc/passwd"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.sandbox.freeze_test_bundle(env.workspace_id, [path])
        with self.assertRaises(ValueError):
            await self.sandbox.read_artifact("../../etc/passwd")
        with self.assertRaises(ValueError):
            await self.prepare({"idna;whoami": "1"})

    async def test_run_results_coverage_artifacts_and_restart(self):
        env = await self.prepare()
        bundle = await self.bundle(env)
        run = await self.sandbox.run_tests(
            env.environment_id, bundle.bundle_id, idempotency_key="once"
        )
        restored = TestSandbox(self.settings)
        self.assertEqual(run, await restored.get_test_results(run.run_id))
        coverage = await restored.get_coverage(run.run_id, "api.py")
        self.assertEqual(coverage.executed_lines, [1, 2])
        self.assertEqual(coverage.executed_branches, [[1, 2]])
        self.assertEqual(
            await restored.read_artifact(run.stdout_artifact_id, 2, 2), "stderr"
        )
        previous = self.runtime.execute.await_count
        self.assertEqual(
            run,
            await self.sandbox.run_tests(
                env.environment_id, bundle.bundle_id, idempotency_key="once"
            ),
        )
        self.assertEqual(previous, self.runtime.execute.await_count)
        self.assertEqual(run.status, "passed")

    async def test_idempotency_conflict(self):
        env = await self.prepare(idempotency_key="env")
        with self.assertRaises(SandboxError):
            await self.prepare({"idna": "3.11"}, idempotency_key="env")
        bundle = await self.bundle(env)
        await self.sandbox.run_tests(
            env.environment_id, bundle.bundle_id, idempotency_key="run"
        )
        with self.assertRaises(SandboxError):
            await self.sandbox.run_tests(
                env.environment_id, bundle.bundle_id, 5, idempotency_key="run"
            )

    async def test_limits_and_network_opt_in(self):
        env = await self.prepare()
        bundle = await self.bundle(env)
        with self.assertRaises(ValueError):
            await self.sandbox.run_tests(env.environment_id, bundle.bundle_id, 121)
        with self.assertRaises(ValueError):
            await self.sandbox.run_tests(
                env.environment_id, bundle.bundle_id, allow_network=True
            )
        await self.sandbox.run_tests(env.environment_id, bundle.bundle_id)
        self.assertEqual(self.runtime.execute.call_args.kwargs["network"], "none")

    async def test_failure_details_and_confirmation(self):
        before = await self.prepare()
        after = await self.prepare({"idna": "3.11"})
        bundle = await self.bundle(before)
        baseline = await self.sandbox.run_tests(before.environment_id, bundle.bundle_id)
        self.execution_state = {"ExitCode": 1}
        self.execution_report = report("failed")
        candidate = await self.sandbox.run_tests(after.environment_id, bundle.bundle_id)
        self.assertEqual(candidate.failures[0].phase, "call")
        self.assertEqual(candidate.failures[0].error_type, "ValueError")
        self.assertIn(
            "trace",
            await self.sandbox.read_artifact(
                candidate.failures[0].traceback_artifact_id
            ),
        )
        self.assertEqual(
            (
                await self.sandbox.compare_runs(baseline.run_id, candidate.run_id)
            ).verdict,
            "inconclusive",
        )
        confirmation = await self.sandbox.run_tests(
            after.environment_id, bundle.bundle_id
        )
        self.assertEqual(
            (
                await self.sandbox.compare_runs(
                    baseline.run_id,
                    candidate.run_id,
                    confirmation_run_id=confirmation.run_id,
                )
            ).verdict,
            "breaking",
        )

    async def test_comparison_compatible_and_mismatched_bundle(self):
        before = await self.prepare()
        after = await self.prepare({"idna": "3.11"})
        bundle = await self.bundle(before)
        baseline = await self.sandbox.run_tests(before.environment_id, bundle.bundle_id)
        candidate = await self.sandbox.run_tests(after.environment_id, bundle.bundle_id)
        self.assertEqual(
            (
                await self.sandbox.compare_runs(baseline.run_id, candidate.run_id)
            ).verdict,
            "compatible",
        )
        await self.sandbox.write_test(
            before.workspace_id, "test_api.py", "def test_api(): pass"
        )
        changed = await self.sandbox.freeze_test_bundle(
            before.workspace_id, ["generated/test_api.py"]
        )
        candidate = await self.sandbox.run_tests(
            after.environment_id, changed.bundle_id
        )
        self.assertEqual(
            (
                await self.sandbox.compare_runs(baseline.run_id, candidate.run_id)
            ).verdict,
            "inconclusive",
        )

    async def test_baseline_failed_and_blocked(self):
        before = await self.prepare()
        after = await self.prepare({"idna": "3.11"})
        bundle = await self.bundle(before)
        self.execution_state = {"ExitCode": 1}
        self.execution_report = report("failed")
        baseline = await self.sandbox.run_tests(before.environment_id, bundle.bundle_id)
        candidate = await self.sandbox.run_tests(after.environment_id, bundle.bundle_id)
        self.assertEqual(
            (
                await self.sandbox.compare_runs(baseline.run_id, candidate.run_id)
            ).verdict,
            "baseline_failed",
        )
        self.execution_state = {"ExitCode": 137, "OOMKilled": True}
        candidate = await self.sandbox.run_tests(after.environment_id, bundle.bundle_id)
        self.assertEqual(
            (
                await self.sandbox.compare_runs(baseline.run_id, candidate.run_id)
            ).verdict,
            "blocked",
        )

    async def test_cleanup_keeps_reports_and_is_repeatable(self):
        env = await self.prepare()
        bundle = await self.bundle(env)
        run = await self.sandbox.run_tests(env.environment_id, bundle.bundle_id)
        await self.sandbox.cleanup_environment(env.environment_id)
        await self.sandbox.cleanup_environment(env.environment_id)
        self.assertEqual(
            (await self.sandbox.get_environment(env.environment_id)).status, "deleted"
        )
        self.assertEqual(
            (await self.sandbox.get_test_results(run.run_id)).status, "passed"
        )
        self.assertIn(
            "summary", await self.sandbox.read_artifact(run.report_artifact_id)
        )

    async def test_recover_interrupted_operations(self):
        env = await self.prepare()
        bundle = await self.bundle(env)
        with patch.object(self.sandbox, "_runtime", self.runtime):
            self.runtime.execute.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await self.sandbox.run_tests(
                    env.environment_id, bundle.bundle_id, idempotency_key="cancel"
                )
        store = ArtifactStore(self.settings.root, 10_000_000)
        interrupted_id = "b" * 32
        pending = RunDTO(
            run_id=interrupted_id,
            environment_id=env.environment_id,
            test_bundle_id=bundle.bundle_id,
            status="running",
        )
        store.save("runs", interrupted_id, pending.model_dump(mode="json"))
        self.assertEqual(await self.sandbox.recover(), [interrupted_id])
        self.assertEqual(
            (await self.sandbox.get_test_results(interrupted_id)).status, "interrupted"
        )
        self.assertEqual(await self.sandbox.recover(), [])

    async def test_candidate_resume_without_original_checkout(self):
        baseline = await self.prepare(idempotency_key="pinned-baseline")
        import shutil

        shutil.rmtree(self.repo)
        candidate = await self.sandbox.prepare_environment(
            "repo",
            SHA,
            None,
            "3.12",
            {"idna": "3.11"},
            baseline_environment_id=baseline.environment_id,
        )
        self.assertEqual(candidate.status, "ready", candidate.reason)
        self.assertEqual(candidate.baseline_environment_id, baseline.environment_id)

    async def test_skipped_checks_and_flaky_failures_are_inconclusive(self):
        baseline_env = await self.prepare()
        candidate_env = await self.prepare({"idna": "3.11"})
        bundle = await self.bundle(baseline_env)
        baseline = await self.sandbox.run_tests(
            baseline_env.environment_id, bundle.bundle_id
        )
        self.execution_report = report("skipped")
        skipped = await self.sandbox.run_tests(
            candidate_env.environment_id, bundle.bundle_id
        )
        self.assertEqual(
            (await self.sandbox.compare_runs(baseline.run_id, skipped.run_id)).verdict,
            "inconclusive",
        )
        self.execution_state = {"ExitCode": 1}
        self.execution_report = report("failed")
        failed = await self.sandbox.run_tests(
            candidate_env.environment_id, bundle.bundle_id
        )
        self.execution_state = {"ExitCode": 0}
        self.execution_report = report()
        confirmation = await self.sandbox.run_tests(
            candidate_env.environment_id, bundle.bundle_id
        )
        comparison = await self.sandbox.compare_runs(
            baseline.run_id, failed.run_id, confirmation_run_id=confirmation.run_id
        )
        self.assertEqual(comparison.verdict, "inconclusive")

    async def test_cpu_platform_mismatch_is_not_equivalent(self):
        baseline_env = await self.prepare()
        candidate_env = await self.prepare({"idna": "3.11"})
        bundle = await self.bundle(baseline_env)
        baseline = await self.sandbox.run_tests(
            baseline_env.environment_id, bundle.bundle_id
        )
        candidate = await self.sandbox.run_tests(
            candidate_env.environment_id, bundle.bundle_id
        )
        store = ArtifactStore(self.settings.root, 10_000_000)
        changed = candidate_env.model_copy(update={"platform": "linux/amd64"})
        store.save(
            "environments", changed.environment_id, changed.model_dump(mode="json")
        )
        self.assertEqual(
            (
                await self.sandbox.compare_runs(baseline.run_id, candidate.run_id)
            ).verdict,
            "inconclusive",
        )


class ReportTests(unittest.TestCase):
    def test_exit_classification(self):
        with TemporaryDirectory() as root:
            store = ArtifactStore(Path(root), 10_000_000)
            for state, expected in (
                ({"ExitCode": 0}, "passed"),
                ({"ExitCode": 1}, "failed"),
                ({"ExitCode": 2}, "collection_error"),
                ({"ExitCode": 3}, "infrastructure_error"),
                ({"ExitCode": 4}, "infrastructure_error"),
                ({"ExitCode": 5}, "no_tests"),
                ({"ExitCode": 137, "OOMKilled": True}, "resource_limit"),
                ({"ExitCode": 137, "TimedOut": True}, "timeout"),
                ({"ExitCode": 139}, "infrastructure_error"),
            ):
                with self.subTest(state=state):
                    run = RunDTO(
                        run_id="a" * 32,
                        environment_id="b" * 32,
                        test_bundle_id="c" * 64,
                        status="running",
                    )
                    parsed = parse_report(
                        run,
                        state,
                        {"report.json": json.dumps(report()).encode()},
                        store,
                    )
                    self.assertEqual(parsed.status, expected)

    def test_collection_and_missing_malformed_report(self):
        with TemporaryDirectory() as root:
            store = ArtifactStore(Path(root), 10_000_000)
            run = RunDTO(
                run_id="a" * 32,
                environment_id="b" * 32,
                test_bundle_id="c" * 64,
                status="running",
            )
            data = {
                "tests": [],
                "collectors": [
                    {
                        "nodeid": "test_bad.py",
                        "outcome": "failed",
                        "longrepr": "SyntaxError",
                    }
                ],
            }
            result = parse_report(
                run, {"ExitCode": 2}, {"report.json": json.dumps(data).encode()}, store
            )
            self.assertEqual(result.failures[0].phase, "collect")
            self.assertEqual(result.errors, 1)
            self.assertEqual(
                parse_report(run, {"ExitCode": 0}, {}, store).status,
                "infrastructure_error",
            )
            with self.assertRaises(SandboxError):
                parse_report(run, {"ExitCode": 0}, {"report.json": b"bad json"}, store)

    def test_special_files_and_size_limit(self):
        with TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            (source / "link").symlink_to("/etc/passwd")
            with self.assertRaises(SandboxError):
                snapshot_repository(source, Path(root) / "copy", 100)
            (source / "link").unlink()
            (source / "large").write_bytes(b"x" * 101)
            with self.assertRaises(SandboxError):
                snapshot_repository(source, Path(root) / "copy", 100)

    def test_safe_paths(self):
        for path in ("../x", "/x", "x//y", "x/./y", "", "--help", "x:y"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(path)


class DockerContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_limits_mounts_and_no_host_secrets(self):
        settings = SandboxSettings(
            limits=SandboxLimits(cpus=0.5, memory_mb=512, pids_limit=64), _env_file=None
        )
        runtime = DockerRuntime(settings)
        calls = []

        async def command(arguments, **kwargs):
            calls.append(arguments)
            if arguments[0] == "inspect":
                return 0, b'{"ExitCode":0}'
            if arguments[0] == "cp":
                return 1, b"missing"
            return 0, b""

        with patch.object(runtime, "_command", side_effect=command):
            await runtime.execute(
                "a" * 32,
                "sha256:image",
                "test",
                {"environment_id": "b" * 32},
                timeout=10,
                network="none",
            )
        create = calls[0]
        for option in (
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit",
            "--cpus",
            "--memory",
            "--network",
        ):
            self.assertIn(option, create)
        self.assertEqual(create[create.index("--cpus") + 1], "0.5")
        self.assertEqual(create[create.index("--memory") + 1], "512m")
        self.assertEqual(create[create.index("--network") + 1], "none")
        self.assertEqual(create[create.index("--user") + 1], "1000:1000")
        self.assertIn("readonly", " ".join(create))
        self.assertNotIn("docker.sock", " ".join(create))
        self.assertNotIn(str(Path.home()), " ".join(create))
        self.assertNotIn("--privileged", create)

    async def test_disallowed_network(self):
        runtime = DockerRuntime(SandboxSettings(_env_file=None))
        for network in ("host", "bridge", "default"):
            with self.assertRaises(ValueError):
                await runtime.execute(
                    "a" * 32,
                    "image",
                    "test",
                    {"environment_id": "b" * 32},
                    timeout=1,
                    network=network,
                )

    async def test_symlink_artifact_rejected(self):
        runtime = DockerRuntime(SandboxSettings(_env_file=None))
        stream = BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            entry = tarfile.TarInfo("report.json")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/passwd"
            archive.addfile(entry)

        async def command(arguments, **kwargs):
            if arguments[0] == "inspect":
                return 0, b'{"ExitCode":0}'
            return 0, stream.getvalue() if arguments[0] == "cp" else b""

        with (
            patch.object(runtime, "_command", side_effect=command),
            self.assertRaises(SandboxError),
        ):
            await runtime.execute(
                "a" * 32,
                "image",
                "test",
                {"environment_id": "b" * 32},
                timeout=1,
                network="none",
            )
