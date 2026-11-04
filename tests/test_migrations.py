import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.sqlite3"

    def migrate(self, *args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "DATABASE_URL": f"sqlite+aiosqlite:///{self.path}",
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_upgrade_and_constraints(self):
        self.migrate("upgrade", "head")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("INSERT INTO repos (id,name,repo_url) VALUES (1,'test','url')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO monitoring_runs (repo_id,started_at,state) "
                    "VALUES (999,'2026-01-01','running')"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO monitoring_runs (repo_id,started_at,state) "
                    "VALUES (1,'2026-01-01','wrong')"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO monitoring_runs (repo_id,started_at,state) "
                    "VALUES (1,'2026-01-01','failed')"
                )
            db.execute(
                "INSERT INTO monitoring_runs (id,repo_id,started_at,state) "
                "VALUES (1,1,'2026-01-01','running')"
            )
            for status in ("incompat", "review", "failed", "waiting", "low", "current"):
                db.execute(
                    "INSERT INTO dependency_results "
                    "(run_id,name,current_version,status) VALUES (1,?,'1.0',?)",
                    (status, status),
                )
            for run_id, name, status in (
                (999, "a", "low"),
                (1, "a", "wrong"),
                (1, "low", "low"),
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        "INSERT INTO dependency_results "
                        "(run_id,name,current_version,status) VALUES (?,?,'1.0',?)",
                        (run_id, name, status),
                    )

    def test_baseline_data_survives_upgrade_and_downgrade(self):
        self.migrate("upgrade", "0001_baseline")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO repos (id,name,repo_url) VALUES (1,'test','url')")
            db.execute(
                "INSERT INTO dependencies (repo_id,name,current_version) "
                "VALUES (1,'requests','1.0')"
            )
        self.migrate("upgrade", "head")
        self.migrate("downgrade", "0001_baseline")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(
                db.execute("SELECT name FROM repos").fetchall(), [("test",)]
            )
            self.assertEqual(
                db.execute("SELECT name FROM dependencies").fetchall(), [("requests",)]
            )
            self.assertFalse(
                db.execute(
                    "SELECT name FROM sqlite_master WHERE name='monitoring_runs'"
                ).fetchall()
            )

    def test_existing_unversioned_database_stamp(self):
        self.migrate("upgrade", "0001_baseline")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO repos (id,name,repo_url) VALUES (1,'existing','url')"
            )
            db.execute("DROP TABLE alembic_version")
        self.migrate("stamp", "0001_baseline")
        self.migrate("upgrade", "head")
        self.migrate("current")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(
                db.execute("SELECT name FROM repos").fetchone()[0], "existing"
            )

    def test_repository_identity_keeps_all_history_across_upgrade_and_downgrade(self):
        self.migrate("upgrade", "0002_monitoring_state")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO repos (id,name,repo_url) VALUES (7,'legacy','url')")
            db.execute(
                "INSERT INTO dependencies (id,repo_id,name,current_version) VALUES (9,7,'requests','1')"
            )
            db.execute(
                "INSERT INTO monitoring_runs (id,repo_id,started_at,finished_at,state) VALUES (11,7,'2026-01-01','2026-01-01','succeeded')"
            )
            db.execute(
                "INSERT INTO dependency_results (id,run_id,name,current_version,status) VALUES (13,11,'requests','1','review')"
            )
        self.migrate("upgrade", "head")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(
                db.execute("SELECT id,github_id FROM repos").fetchall(), [(7, None)]
            )
            db.execute(
                "UPDATE repos SET github_id=123,deleted_at='2026-01-02' WHERE id=7"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO repos (name,repo_url,github_id) VALUES ('duplicate','url',123)"
                )
        self.migrate("downgrade", "0002_monitoring_state")
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(
                db.execute("SELECT id,name FROM repos").fetchall(), [(7, "legacy")]
            )
            self.assertEqual(
                db.execute("SELECT id,repo_id FROM dependencies").fetchall(), [(9, 7)]
            )
            self.assertEqual(
                db.execute("SELECT id,repo_id FROM monitoring_runs").fetchall(),
                [(11, 7)],
            )
            self.assertEqual(
                db.execute("SELECT id,run_id FROM dependency_results").fetchall(),
                [(13, 11)],
            )
            self.assertNotIn(
                "github_id", [r[1] for r in db.execute("PRAGMA table_info(repos)")]
            )
