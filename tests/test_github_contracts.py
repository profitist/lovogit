import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from lovogit.app.providers import ResolvedRepoDTO, SourceSnapshotDTO
from lovogit.app.providers.errors import SourceUnavailable


class ContractTests(unittest.TestCase):
    def test_snapshot_has_immutable_revision(self):
        repo = ResolvedRepoDTO(
            1, "team/project", "https://github.com/team/project", "main", "a" * 40
        )
        snapshot = SourceSnapshotDTO(repo, Path("/tmp/example"))
        self.assertEqual(snapshot.repo.commit_sha, "a" * 40)
        with self.assertRaises(FrozenInstanceError):
            repo.commit_sha = "b" * 40

    def test_import_does_not_load_global_settings(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import lovogit.app.providers; import sys; "
                    'assert "lovogit.cfg.settings" not in sys.modules'
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class ErrorTests(unittest.TestCase):
    def test_error_keeps_context_separate_from_user_message(self):
        error = SourceUnavailable(stage="archive", status_code=503)
        self.assertEqual(error.user_message, "GitHub временно недоступен")
        self.assertIn("stage=archive", str(error))
        self.assertIn("status_code=503", str(error))
        self.assertNotIn("503", error.user_message)
