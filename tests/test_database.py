import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text

from lovogit.db.engine import make_engine

ROOT = Path(__file__).resolve().parents[1]


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_keys_enabled(self):
        engine = make_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.connect() as connection:
                self.assertEqual(
                    await connection.scalar(text("PRAGMA foreign_keys")), 1
                )
        finally:
            await engine.dispose()

    def test_alembic_without_bot_or_llm_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("BOT_", "LLM_", "MODEL_"))
            }
            env["PYTHONPATH"] = str(ROOT / "src")
            env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}/test.sqlite3"
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "current"],
                cwd=ROOT,
                check=False,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
