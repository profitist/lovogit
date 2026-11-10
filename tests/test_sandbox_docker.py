import importlib.util
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lovogit.cfg.sandbox import SandboxLimits, SandboxSettings
from lovogit.sandbox import TestSandbox

if importlib.util.find_spec("pytest"):
    import pytest

    pytestmark = pytest.mark.docker


@unittest.skipUnless(
    os.environ.get("SANDBOX_DOCKER_TESTS") == "1",
    "Set SANDBOX_DOCKER_TESTS=1 to run Docker integration tests",
)
class DockerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise RuntimeError("Docker integration requested but Docker CLI is missing")
        subprocess.run(["docker", "info"], check=True, stdout=subprocess.DEVNULL)

    async def asyncSetUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname="sandbox-fixture"\nversion="0.0.1"\nrequires-python=">=3.12"\ndependencies=["idna>=3.10,<4"]\n'
        )
        (self.repo / "api.py").write_text("def value():\n    return 42\n")
        (self.repo / ".env").write_text("SANDBOX_HOST_SECRET=must-not-be-visible")
        self.settings = SandboxSettings(
            root=self.root / "store",
            image=os.environ.get("SANDBOX_TEST_IMAGE", "lovogit-sandbox:test"),
            limits=SandboxLimits(cpus=0.5, memory_mb=256, timeout_seconds=10),
            preparation_timeout_seconds=120,
            _env_file=None,
        )
        self.sandbox = TestSandbox(self.settings)
        self.environments = []
        self.env = await self.prepare()
        self.assertEqual(self.env.status, "ready", self.env.reason)

    async def asyncTearDown(self):
        for identifier in self.environments:
            await self.sandbox.cleanup_environment(identifier)
        self.temporary.cleanup()

    async def prepare(self, overrides=None):
        env = await self.sandbox.prepare_environment(
            "fixture", "a" * 40, str(self.repo), "3.12", overrides
        )
        self.environments.append(env.environment_id)
        return env

    async def run_code(self, content, timeout=10):
        artifact = await self.sandbox.write_test(
            self.env.workspace_id, "test_generated.py", content
        )
        bundle = await self.sandbox.freeze_test_bundle(
            self.env.workspace_id, [artifact.path]
        )
        return await self.sandbox.run_tests(
            self.env.environment_id, bundle.bundle_id, timeout
        )

    async def test_baseline_candidate_versions_bundle_and_retention(self):
        artifact = await self.sandbox.write_test(
            self.env.workspace_id,
            "test_generated.py",
            "import idna\nfrom api import value\ndef test_api():\n    assert idna.__version__ in {'3.10','3.11'}\n    assert value() == 42\n",
        )
        bundle = await self.sandbox.freeze_test_bundle(
            self.env.workspace_id, [artifact.path]
        )
        baseline = await self.sandbox.run_tests(
            self.env.environment_id, bundle.bundle_id, 10, idempotency_key="baseline"
        )
        candidate = await self.prepare({"idna": "3.10"})
        self.assertEqual(candidate.status, "ready", candidate.reason)
        self.assertEqual(candidate.installed_dependencies["idna"], "3.10")
        self.assertEqual(candidate.dependency_changes["idna"][1], "3.10")
        result = await self.sandbox.run_tests(
            candidate.environment_id, bundle.bundle_id, 10
        )
        self.assertEqual(baseline.status, "passed", baseline.reason)
        self.assertEqual(result.status, "passed", result.reason)
        self.assertEqual(
            (await self.sandbox.compare_runs(baseline.run_id, result.run_id)).verdict,
            "compatible",
        )
        self.assertIn(
            2,
            (await self.sandbox.get_coverage(baseline.run_id, "api.py")).executed_lines,
        )
        restored = TestSandbox(self.settings)
        self.assertEqual(
            await restored.run_tests(
                self.env.environment_id,
                bundle.bundle_id,
                10,
                idempotency_key="baseline",
            ),
            baseline,
        )
        await self.sandbox.cleanup_environment(candidate.environment_id)
        self.assertIn(
            "tests", await self.sandbox.read_artifact(result.report_artifact_id)
        )
        self.assertFalse((self.repo / "uv.lock").exists())

    async def test_pytest_failure_collection_and_no_tests(self):
        for code, status in (
            ("def test_fail(): assert False\n", "failed"),
            ("def test_broken(:\n", "collection_error"),
            ("VALUE = 1\n", "no_tests"),
        ):
            with self.subTest(status=status):
                result = await self.run_code(code)
                self.assertEqual(result.status, status, result.reason)

    async def test_timeout(self):
        result = await self.run_code(
            "import time\ndef test_sleep(): time.sleep(60)\n", timeout=2
        )
        self.assertEqual(result.status, "timeout")

    async def test_memory_limit(self):
        result = await self.run_code(
            "def test_memory():\n    chunks = []\n    while True: chunks.append(bytearray(16*1024*1024))\n"
        )
        self.assertEqual(result.status, "resource_limit", result)

    async def test_cpu_quota(self):
        result = await self.run_code(
            "from pathlib import Path\ndef test_cpu():\n    quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()\n    assert int(quota) / int(period) == 0.5\n"
        )
        self.assertEqual(result.status, "passed", result)

    async def test_isolation_network_secrets_and_readonly(self):
        result = await self.run_code("""import os
import socket
from pathlib import Path
import pytest

def test_isolation():
    assert os.getuid() == 1000
    assert "SANDBOX_HOST_SECRET" not in os.environ
    assert not Path("/workspace/repository/.env").exists()
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/root/.ssh").exists()
    status = Path("/proc/self/status").read_text()
    assert "NoNewPrivs:\\t1" in status
    assert "CapEff:\\t0000000000000000" in status
    with pytest.raises(OSError):
        Path("/workspace/repository/injected.py").write_text("bad")
    with pytest.raises(OSError):
        Path("/etc/injected").write_text("bad")
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 443), timeout=1)
""")
        self.assertEqual(result.status, "passed", result)

    async def test_incompatible_constraint_blocked(self):
        candidate = await self.prepare({"idna": "4.0"})
        self.assertEqual(candidate.status, "blocked")
