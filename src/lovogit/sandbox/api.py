import asyncio
import hashlib
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lovogit.cfg.sandbox import SandboxSettings

from .docker import DockerRuntime
from .models import (
    ComparisonResult as ComparisonResultDTO,
)
from .models import (
    CoverageResult as CoverageResultDTO,
)
from .models import (
    EnvironmentInfo as EnvironmentInfoDTO,
)
from .models import (
    TestArtifact as TestArtifactDTO,
)
from .models import (
    TestBundle as TestBundleDTO,
)
from .models import (
    TestRunResult as TestRunResultDTO,
)
from .reports import parse_report
from .storage import (
    ArtifactStore,
    SandboxError,
    fingerprint,
    safe_path,
    snapshot_repository,
)


class TestSandbox:
    """Persistent Python test execution API. No repository code executes on the host."""

    __test__ = False

    def __init__(self, settings: SandboxSettings | None = None):
        self._settings = settings or SandboxSettings()
        self._store = ArtifactStore(
            self._settings.root, self._settings.max_artifact_bytes
        )
        self._runtime = DockerRuntime(self._settings)

    async def prepare_environment(
        self,
        repository_id: str,
        repository_sha: str,
        repository_path: str | None,
        python_version: str,
        dependency_overrides: dict[str, str] | None = None,
        *,
        idempotency_key: str | None = None,
        baseline_environment_id: str | None = None,
    ) -> EnvironmentInfoDTO:
        if not repository_id or not re.fullmatch(r"[a-fA-F0-9]{40}", repository_sha):
            raise ValueError(
                "Environment preparation requires repository ID and full commit SHA"
            )
        if not re.fullmatch(r"3\.(?:1[1-9]|[2-9][0-9])(?:\.[0-9]+)?", python_version):
            raise ValueError("Sandbox Python version must be 3.11 or newer")
        overrides = {}
        for name, version in (dependency_overrides or {}).items():
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*", name
            ) or not re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]*", version):
                raise ValueError(
                    "Dependency overrides require package names and exact versions"
                )
            overrides[re.sub(r"[-_.]+", "-", name).lower()] = version
        baseline = None
        if baseline_environment_id:
            if not overrides:
                raise ValueError(
                    "A baseline reference requires candidate dependency overrides"
                )
            baseline = await self.get_environment(baseline_environment_id)
        elif overrides:
            baseline = await self.prepare_environment(
                repository_id, repository_sha, repository_path, python_version
            )
        if repository_path is None:
            if baseline is None:
                raise ValueError("Repository path is required for baseline preparation")
            repository_path = str(
                self._store.path("snapshots", baseline.environment_id)
            )
        source = Path(repository_path).resolve(strict=True)
        if not source.is_dir():
            raise ValueError("Repository path must be a directory")
        async with self._store.lock():
            temporary_id = uuid4().hex
            snapshot = self._store.path("snapshots", temporary_id)
            source_hash, lock_hash = await asyncio.to_thread(
                snapshot_repository, source, snapshot, self._settings.max_snapshot_bytes
            )
            identity = {
                "repository_id": repository_id,
                "repository_sha": repository_sha.lower(),
                "python_version": python_version,
                "source_hash": source_hash,
                "dependency_overrides": overrides,
                "image": self._settings.image,
                "network": self._settings.preparation_network,
                "baseline_environment_id": baseline.environment_id
                if baseline
                else None,
            }
            identifier = fingerprint({"key": idempotency_key, "identity": identity})
            if idempotency_key:
                key_id = fingerprint(["prepare", idempotency_key])
                key_path = self._store.path("keys", key_id)
                if (
                    key_path.exists()
                    and self._store.load("keys", key_id)["identity"] != identity
                ):
                    shutil.rmtree(snapshot)
                    raise SandboxError(
                        "Preparation idempotency key reused with different inputs"
                    )
                self._store.save("keys", key_id, {"identity": identity})
            metadata = self._store.path("environments", identifier)
            if metadata.exists():
                existing = EnvironmentInfoDTO.model_validate(
                    self._store.load("environments", identifier)
                )
                if existing.status in {"ready", "blocked"}:
                    shutil.rmtree(snapshot)
                    return existing
                await self._runtime.cleanup(identifier, environment=True)
            canonical = self._store.path("snapshots", identifier)
            if canonical.exists():
                shutil.rmtree(canonical)
            snapshot.rename(canonical)
            environment = EnvironmentInfoDTO(
                environment_id=identifier,
                workspace_id=identifier,
                repository_id=repository_id,
                repository_sha=repository_sha.lower(),
                python_version=python_version,
                dependency_overrides=overrides,
                baseline_environment_id=baseline.environment_id if baseline else None,
                status="preparing",
                fingerprint=fingerprint(identity),
                source_hash=source_hash,
                lockfile_hash=lock_hash,
                variables={
                    "PYTHONPATH": "/workspace/repository:/workspace/repository/src",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            self._store.save(
                "environments", identifier, environment.model_dump(mode="json")
            )
            try:
                if baseline and (
                    baseline.status != "ready"
                    or baseline.source_hash != source_hash
                    or baseline.repository_id != repository_id
                    or baseline.repository_sha != repository_sha.lower()
                ):
                    raise SandboxError(
                        "Candidate requires a ready baseline with identical repository content"
                    )
                image, platform = await self._runtime.image_info()
                environment = environment.model_copy(
                    update={"image_digest": image, "platform": platform}
                )
                if baseline:
                    resolved = self._store.load("resolved", baseline.environment_id)
                    lockname = (
                        "uv.lock"
                        if (canonical / "pyproject.toml").exists()
                        else "sandbox-resolved.txt"
                    )
                    (canonical / lockname).write_bytes(
                        self._store.read(resolved["artifact_id"])
                    )
                request = {
                    "environment_id": identifier,
                    "python_version": python_version,
                    "dependency_overrides": overrides,
                }
                state, output, files = await self._runtime.execute(
                    identifier,
                    image,
                    "prepare",
                    request,
                    source=canonical,
                    timeout=self._settings.preparation_timeout_seconds,
                    network=self._settings.preparation_network,
                )
                log_id = self._store.artifact(output)
                if (
                    state.get("ExitCode") != 0
                    or state.get("TimedOut")
                    or state.get("OOMKilled")
                    or "preparation.json" not in files
                    or "resolved.lock" not in files
                ):
                    raise SandboxError(
                        f"Environment preparation failed; log artifact {log_id}"
                    )
                info = json.loads(files["preparation.json"])
                installed = info["installed_dependencies"]
                if baseline and (
                    baseline.python_version != info["python_version"]
                    or baseline.platform != platform
                    or baseline.image_digest != image
                ):
                    raise SandboxError(
                        "Candidate platform, image or Python differs from baseline"
                    )
                changes = (
                    {
                        name: (
                            baseline.installed_dependencies.get(name),
                            installed.get(name),
                        )
                        for name in sorted(
                            baseline.installed_dependencies.keys() | installed.keys()
                        )
                        if baseline.installed_dependencies.get(name)
                        != installed.get(name)
                    }
                    if baseline
                    else {}
                )
                for package, version in overrides.items():
                    if installed.get(package) != version:
                        raise SandboxError(
                            f"Requested version was not installed: {package}=={version}"
                        )
                resolved_id = self._store.artifact(files["resolved.lock"])
                self._store.save("resolved", identifier, {"artifact_id": resolved_id})
                environment = environment.model_copy(
                    update={
                        "status": "ready",
                        "python_version": info["python_version"],
                        "installed_dependencies": installed,
                        "dependency_changes": changes,
                        "preparation_artifact_id": log_id,
                        "fingerprint": fingerprint(
                            [
                                identity,
                                image,
                                platform,
                                installed,
                                resolved_id,
                                environment.variables,
                            ]
                        ),
                    }
                )
            except (SandboxError, ValueError, KeyError) as exc:
                environment = environment.model_copy(
                    update={"status": "blocked", "reason": str(exc)[:1000]}
                )
                try:
                    await self._runtime.cleanup(identifier, environment=True)
                except SandboxError:
                    pass
            self._store.save(
                "environments", identifier, environment.model_dump(mode="json")
            )
            return environment

    async def get_environment(self, environment_id: str) -> EnvironmentInfoDTO:
        return EnvironmentInfoDTO.model_validate(
            self._store.load("environments", environment_id)
        )

    async def write_test(
        self, workspace_id: str, path: str, content: str
    ) -> TestArtifactDTO:
        relative = safe_path(path)
        if not relative.endswith(".py") or "::" in relative:
            raise ValueError("Generated tests must be Python files")
        data = content.encode("utf-8")
        async with self._store.lock():
            environment = await self.get_environment(workspace_id)
            if environment.status == "deleted":
                raise SandboxError(f"Workspace has been deleted: {workspace_id}")
            directory = self._store.path("workspaces", workspace_id)
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            artifact_id = self._store.artifact(data)
            target.write_bytes(data)
            return TestArtifactDTO(
                path=f"generated/{relative}",
                sha256=hashlib.sha256(data).hexdigest(),
                artifact_id=artifact_id,
            )

    async def freeze_test_bundle(
        self, workspace_id: str, test_paths: list[str]
    ) -> TestBundleDTO:
        if not test_paths or len(test_paths) > 1000:
            raise ValueError("Bundle requires between 1 and 1000 test selectors")
        async with self._store.lock():
            environment = await self.get_environment(workspace_id)
            tests = []
            generated = {}
            for selector in sorted(set(test_paths)):
                path, *node = selector.split("::", 1)
                safe_path(path)
                if node and (
                    not node[0] or any(c in node[0] for c in ("\x00", "\n", "\r", "\\"))
                ):
                    raise ValueError(f"Invalid pytest node selector: {selector!r}")
                if path.startswith("generated/"):
                    relative = safe_path(path[len("generated/") :])
                    target = self._store.path("workspaces", workspace_id) / relative
                    data = target.read_bytes()
                    generated[relative] = data
                elif path.startswith("repository/"):
                    relative = safe_path(path[len("repository/") :])
                    data = (
                        self._store.path("snapshots", workspace_id) / relative
                    ).read_bytes()
                else:
                    raise ValueError(
                        "Selectors must start with generated/ or repository/"
                    )
                if not path.endswith(".py"):
                    raise ValueError("Bundle selectors must reference Python files")
                artifact_id = self._store.artifact(data)
                tests.append(
                    TestArtifactDTO(
                        path=selector,
                        sha256=hashlib.sha256(data).hexdigest(),
                        artifact_id=artifact_id,
                    )
                )
            bundle_hash = fingerprint(
                [
                    environment.repository_id,
                    environment.repository_sha,
                    environment.source_hash,
                    [test.model_dump() for test in tests],
                ]
            )
            bundle = TestBundleDTO(
                bundle_id=bundle_hash,
                repository_id=environment.repository_id,
                repository_sha=environment.repository_sha,
                source_hash=environment.source_hash,
                tests=tests,
                fingerprint=bundle_hash,
                created_at=datetime.now(UTC),
            )
            if self._store.path("bundles", bundle_hash).exists():
                return TestBundleDTO.model_validate(
                    self._store.load("bundles", bundle_hash)
                )
            directory = self._store.path("bundle_files", bundle_hash)
            directory.mkdir(exist_ok=True)
            for path, data in generated.items():
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o444)
            self._store.save("bundles", bundle_hash, bundle.model_dump(mode="json"))
            return bundle

    async def run_tests(
        self,
        environment_id: str,
        test_bundle_id: str,
        timeout_seconds: int | None = None,
        *,
        idempotency_key: str | None = None,
        allow_network: bool = False,
    ) -> TestRunResultDTO:
        timeout_seconds = (
            self._settings.limits.timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if not 1 <= timeout_seconds <= self._settings.limits.timeout_seconds:
            raise ValueError("Requested timeout exceeds configured sandbox limit")
        network = self._settings.integration_network if allow_network else "none"
        if not network:
            raise ValueError(
                "Network tests require an explicitly configured integration network"
            )
        async with self._store.lock():
            environment = await self.get_environment(environment_id)
            bundle = TestBundleDTO.model_validate(
                self._store.load("bundles", test_bundle_id)
            )
            if (bundle.repository_id, bundle.repository_sha, bundle.source_hash) != (
                environment.repository_id,
                environment.repository_sha,
                environment.source_hash,
            ):
                raise SandboxError(
                    "Test bundle does not match environment repository snapshot"
                )
            for test in bundle.tests:
                if test.path.startswith("generated/"):
                    target = (
                        self._store.path("bundle_files", bundle.bundle_id)
                        / test.path.split("::")[0][10:]
                    )
                    if hashlib.sha256(target.read_bytes()).hexdigest() != test.sha256:
                        raise SandboxError("Frozen test bundle was modified")
            identity = [environment_id, test_bundle_id, timeout_seconds, network]
            if idempotency_key:
                key_id = fingerprint(["run", idempotency_key])
                key_path = self._store.path("keys", key_id)
                if (
                    key_path.exists()
                    and self._store.load("keys", key_id)["identity"] != identity
                ):
                    raise SandboxError(
                        "Run idempotency key reused with different inputs"
                    )
                self._store.save("keys", key_id, {"identity": identity})
            run_id = (
                fingerprint([identity, idempotency_key])
                if idempotency_key
                else uuid4().hex
            )
            metadata = self._store.path("runs", run_id)
            if metadata.exists():
                previous = await self.get_test_results(run_id)
                if previous.status != "running":
                    return previous
                await self._runtime.cleanup(run_id)
            result = TestRunResultDTO(
                run_id=run_id,
                environment_id=environment_id,
                test_bundle_id=test_bundle_id,
                status="running",
                network=network,
            )
            self._store.save("runs", run_id, result.model_dump(mode="json"))
            started = time.monotonic()
            try:
                if environment.status != "ready":
                    result = result.model_copy(
                        update={
                            "status": "preparation_error",
                            "reason": environment.reason or environment.status,
                        }
                    )
                else:
                    request = {
                        "environment_id": environment_id,
                        "test_paths": [test.path for test in bundle.tests],
                    }
                    state, output, files = await self._runtime.execute(
                        run_id,
                        environment.image_digest,
                        "test",
                        request,
                        bundle=self._store.path("bundle_files", test_bundle_id),
                        timeout=timeout_seconds,
                        network=network,
                    )
                    result = result.model_copy(
                        update={"stdout_artifact_id": self._store.artifact(output)}
                    )
                    result = parse_report(result, state, files, self._store)
            except (SandboxError, OSError) as exc:
                result = result.model_copy(
                    update={"status": "infrastructure_error", "reason": str(exc)[:1000]}
                )
            except asyncio.CancelledError:
                result = result.model_copy(
                    update={
                        "status": "interrupted",
                        "reason": "Run cancelled; use a new idempotency key to retry",
                    }
                )
                self._store.save("runs", run_id, result.model_dump(mode="json"))
                raise
            result = result.model_copy(
                update={"duration_seconds": time.monotonic() - started}
            )
            self._store.save("runs", run_id, result.model_dump(mode="json"))
            return result

    async def get_test_results(self, run_id: str) -> TestRunResultDTO:
        return TestRunResultDTO.model_validate(self._store.load("runs", run_id))

    async def get_coverage(
        self, run_id: str, file_path: str | None = None
    ) -> CoverageResultDTO:
        run = await self.get_test_results(run_id)
        if not run.coverage_artifact_id:
            raise SandboxError(f"Coverage is unavailable for run {run_id}")
        try:
            report = json.loads(self._store.read(run.coverage_artifact_id))
            if file_path is None:
                return CoverageResultDTO(
                    coverage_percent=report["totals"]["percent_covered"]
                )
            relative = safe_path(file_path)
            for filename, entry in report["files"].items():
                normalized = filename.removeprefix("/workspace/repository/")
                if normalized == relative:
                    return CoverageResultDTO(
                        file=relative,
                        executed_lines=entry["executed_lines"],
                        missing_lines=entry["missing_lines"],
                        executed_branches=entry.get("executed_branches", []),
                        missing_branches=entry.get("missing_branches", []),
                        coverage_percent=entry["summary"]["percent_covered"],
                    )
        except (ValueError, KeyError, TypeError) as exc:
            raise SandboxError(f"Invalid coverage report for run {run_id}") from exc
        raise SandboxError(f"File {file_path!r} has no coverage in run {run_id}")

    async def read_artifact(
        self,
        artifact_id: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        start = 1 if start_line is None else start_line
        if start < 1 or (end_line is not None and end_line < start):
            raise ValueError("Artifact line range must be positive and ordered")
        lines = (
            self._store.read(artifact_id).decode("utf-8", errors="replace").splitlines()
        )
        text = "\n".join(lines[start - 1 : end_line])
        return text.encode("utf-8")[: self._settings.limits.max_output_bytes].decode(
            "utf-8", errors="ignore"
        )

    async def compare_runs(
        self,
        baseline_run_id: str,
        candidate_run_id: str,
        *,
        confirmation_run_id: str | None = None,
    ) -> ComparisonResultDTO:
        baseline = await self.get_test_results(baseline_run_id)
        candidate = await self.get_test_results(candidate_run_id)
        before = await self.get_environment(baseline.environment_id)
        after = await self.get_environment(candidate.environment_id)
        differences = {
            name: [getattr(before, name), getattr(after, name)]
            for name in (
                "python_version",
                "platform",
                "image_digest",
                "installed_dependencies",
                "variables",
            )
            if getattr(before, name) != getattr(after, name)
        }
        evidence = [baseline.run_id, candidate.run_id]
        changed = {
            node: (baseline.outcomes.get(node), candidate.outcomes.get(node))
            for node in sorted(baseline.outcomes.keys() | candidate.outcomes.keys())
            if baseline.outcomes.get(node) != candidate.outcomes.get(node)
            or baseline.phases.get(node) != candidate.phases.get(node)
        }
        introduced = [
            node
            for node in changed
            if baseline.outcomes.get(node) == "passed"
            and candidate.outcomes.get(node) in {"failed", "error"}
        ]
        verdict = "inconclusive"
        explanation = "Runs are not equivalent or provide insufficient evidence"
        equivalent = (
            baseline.test_bundle_id == candidate.test_bundle_id
            and (before.repository_id, before.repository_sha, before.source_hash)
            == (after.repository_id, after.repository_sha, after.source_hash)
            and not any(
                name in differences
                for name in ("python_version", "platform", "image_digest", "variables")
            )
            and baseline.network == candidate.network
        )
        blocked = {
            "preparation_error",
            "infrastructure_error",
            "resource_limit",
            "timeout",
            "running",
            "interrupted",
        }
        if not equivalent:
            explanation = "Repository, bundle, Python, platform, image, variables or network differ"
        elif baseline.status in blocked or candidate.status in blocked:
            verdict, explanation = (
                "blocked",
                "Environment or execution failure prevents comparison",
            )
        elif baseline.status in {"failed", "collection_error"}:
            verdict, explanation = (
                "baseline_failed",
                "Baseline already contains test failures",
            )
        elif baseline.status == "passed" and baseline.passed > 0:
            if candidate.status == "passed" and not changed:
                verdict, explanation = (
                    "compatible",
                    "The specified checks detected no new problems; this is not a general compatibility guarantee",
                )
            elif (
                introduced or candidate.status == "collection_error"
            ) and confirmation_run_id:
                confirmation = await self.get_test_results(confirmation_run_id)
                if (
                    confirmation.run_id != candidate.run_id
                    and confirmation.environment_id == candidate.environment_id
                    and confirmation.test_bundle_id == candidate.test_bundle_id
                    and confirmation.network == candidate.network
                    and confirmation.status == candidate.status
                    and confirmation.outcomes == candidate.outcomes
                    and confirmation.phases == candidate.phases
                    and [
                        (f.node_id, f.phase, f.error_type, f.message)
                        for f in confirmation.failures
                    ]
                    == [
                        (f.node_id, f.phase, f.error_type, f.message)
                        for f in candidate.failures
                    ]
                ):
                    verdict, explanation = (
                        "breaking",
                        "New failures reproduced in an independent candidate run",
                    )
                    evidence.append(confirmation.run_id)
            elif introduced:
                explanation = "New test failures require a separate confirmation run to establish reproducibility"
        delta = None
        coverage_changes = {}
        if baseline.coverage_artifact_id and candidate.coverage_artifact_id:
            delta = (await self.get_coverage(candidate.run_id)).coverage_percent - (
                await self.get_coverage(baseline.run_id)
            ).coverage_percent
            baseline_coverage = json.loads(
                self._store.read(baseline.coverage_artifact_id)
            )
            candidate_coverage = json.loads(
                self._store.read(candidate.coverage_artifact_id)
            )
            for filename, entry in baseline_coverage.get("files", {}).items():
                executed_before = set(entry.get("executed_lines", []))
                executed_after = set(
                    candidate_coverage.get("files", {})
                    .get(filename, {})
                    .get("executed_lines", [])
                )
                lost = sorted(executed_before - executed_after)
                if lost:
                    coverage_changes[
                        filename.removeprefix("/workspace/repository/")
                    ] = lost
            if verdict == "compatible" and (delta < 0 or coverage_changes):
                verdict, explanation = (
                    "inconclusive",
                    "Tests pass but coverage decreased; review affected lines and branches",
                )
        return ComparisonResultDTO(
            verdict=verdict,
            explanation=explanation,
            introduced_failures=introduced,
            changed_tests=changed,
            environment_differences=differences,
            coverage_delta=delta,
            coverage_changes=coverage_changes,
            evidence_ids=evidence,
        )

    async def cleanup_environment(self, environment_id: str) -> None:
        async with self._store.lock():
            environment = await self.get_environment(environment_id)
            await self._runtime.cleanup(environment_id, environment=True)
            for group in ("snapshots", "workspaces"):
                path = self._store.path(group, environment_id)
                if path.exists():
                    shutil.rmtree(path)
            self._store.save(
                "environments",
                environment_id,
                environment.model_copy(update={"status": "deleted"}).model_dump(
                    mode="json"
                ),
            )

    async def recover(self) -> list[str]:
        """Call once on startup; the store lock excludes active operations."""
        recovered = []
        async with self._store.lock():
            for group, model, active in (
                ("runs", TestRunResultDTO, "running"),
                ("environments", EnvironmentInfoDTO, "preparing"),
            ):
                directory = self._settings.root.resolve() / group
                if not directory.exists():
                    continue
                for path in directory.iterdir():
                    if not re.fullmatch(r"[a-f0-9]{32,64}", path.name):
                        continue
                    record = model.model_validate(self._store.load(group, path.name))
                    if record.status == active:
                        await self._runtime.cleanup(
                            path.name, environment=group == "environments"
                        )
                        updates = (
                            {
                                "status": "interrupted",
                                "reason": "Controller stopped during execution",
                            }
                            if group == "runs"
                            else {
                                "status": "blocked",
                                "reason": "Controller stopped during preparation; cleanup and prepare again",
                            }
                        )
                        self._store.save(
                            group,
                            path.name,
                            record.model_copy(update=updates).model_dump(mode="json"),
                        )
                        recovered.append(path.name)
        return recovered
