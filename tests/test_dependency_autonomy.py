import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lovogit.app.dependencies.inventory import read_dependency_inventory
from lovogit.app.providers.github.dto import ResolvedRepoDTO, SourceSnapshotDTO


class InventoryAutonomyTests(unittest.TestCase):
    def test_cold_import_without_application_credentials(self):
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("BOT_", "LLM_", "LANGFUSE_", "GITHUB_", "MODEL_"))
        }
        script = """
import sys
from lovogit.app.dependencies.inventory import read_dependency_inventory
forbidden = ("lovogit.cfg.settings", "lovogit.bot", "lovogit.agent", "lovogit.db", "lovogit.sandbox", "langchain")
assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules for prefix in forbidden)
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_read_uses_no_network_process_or_installed_metadata(self):
        repo = ResolvedRepoDTO(
            1, "team/project", "https://github.com/team/project", "main", "a" * 40
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements.txt").write_text(
                "httpx>=0.27\n-r https://example.org/requirements.txt\n"
            )
            (root / "setup.py").write_text("raise RuntimeError('must not execute')")
            with (
                patch("socket.socket", side_effect=AssertionError("network")),
                patch("subprocess.Popen", side_effect=AssertionError("process")),
                patch("os.system", side_effect=AssertionError("shell")),
                patch(
                    "importlib.metadata.distributions",
                    side_effect=AssertionError("installed metadata"),
                ),
            ):
                result = read_dependency_inventory(SourceSnapshotDTO(repo, root))
            self.assertEqual(result.status, "partial")
            self.assertEqual(result.dependencies[0].name, "httpx")
            self.assertEqual(result.dependencies[0].current_version, None)
            self.assertEqual(result.diagnostics[0].code, "forbidden_path")
