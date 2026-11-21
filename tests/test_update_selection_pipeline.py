import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_update_selection_integration import SelectionHTTPFixtures

from lovogit.app.dependencies.inventory import read_dependency_inventory
from lovogit.app.providers.github.dto import ResolvedRepoDTO, SourceSnapshotDTO
from lovogit.db import DependencyModel, MonitoringRun, RepoModel
from lovogit.db.engine import Base, make_engine

PROJECT = """[project]
name="project"
requires-python=">=3.11"
dependencies=["Example_Pkg<2"]
"""
LOCK = """version=1
revision=3
requires-python=">=3.11"
[[package]]
name="project"
version="0.1.0"
source={virtual="."}
dependencies=[{name="example-pkg"}]
[package.metadata]
requires-dist=[{name="example-pkg",specifier="<2"}]
[[package]]
name="example-pkg"
version="1.5"
source={registry="https://pypi.org/simple"}
"""


class SelectionPipelineTests(SelectionHTTPFixtures):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = ResolvedRepoDTO(
            42, "team/project", "https://github.com/team/project", "main", "a" * 40
        )
        self.snapshot = SourceSnapshotDTO(self.repo, self.root)
        document = (
            Path(__file__).resolve().parents[1] / "docs/dependencies.md"
        ).read_text()
        example = (
            document.split("## От инвентаря к материалам обновления", 1)[1]
            .split("```python\n", 1)[1]
            .split("```", 1)[0]
        )
        self.namespace = {}
        exec(compile(example, "docs/dependencies.md", "exec"), self.namespace)  # noqa: S102
        self.pipeline = self.namespace["select_and_collect"]

    async def test_documented_lock_inventory_to_selected_pair(self):
        (self.root / "pyproject.toml").write_text(PROJECT)
        (self.root / "uv.lock").write_text(LOCK)
        inventory = read_dependency_inventory(self.snapshot)
        self.assertEqual(inventory.dependencies[0].version_state, "locked")
        decisions, materials = await self.pipeline(inventory, self.provider)
        self.assertEqual(len(materials), 1)
        self.assertIs(decisions[0].dependency, inventory.dependencies[0])
        self.assertEqual(
            (
                materials[0].request.package,
                materials[0].request.current_version,
                materials[0].request.target_version,
            ),
            ("example-pkg", "1.5", "3.0"),
        )
        self.assertEqual(materials[0].current.origin.version, "1.5")
        self.assertEqual(materials[0].target.origin.version, "3.0")
        self.assertEqual(
            [request.url.path for request in self.requests],
            [
                "/simple/example-pkg/",
                "/pypi/example-pkg/3.0/json",
                "/pypi/example-pkg/1.5/json",
                "/pypi/example-pkg/3.0/json",
            ],
        )

    async def test_current_spelling_and_no_collection_for_skipped_or_no_update(self):
        (self.root / "requirements.txt").write_text("Example_Pkg==1.00\n")
        decisions, materials = await self.pipeline(
            read_dependency_inventory(self.snapshot), self.provider
        )
        self.assertEqual(materials[0].request.current_version, "1.00")
        self.assertEqual(decisions[0].request.current_version, "1.00")
        (self.root / "requirements.txt").write_text(
            "Example_Pkg==3.0\nunknown-package>=1\n"
        )
        collect = AsyncMock(wraps=self.namespace["collect_sources"])
        self.namespace["collect_sources"] = collect
        self.requests.clear()
        decisions, materials = await self.pipeline(
            read_dependency_inventory(self.snapshot), self.provider
        )
        self.assertEqual([item.status for item in decisions], ["no_update", "skipped"])
        self.assertEqual(materials, ())
        collect.assert_not_awaited()
        self.assertEqual(
            [request.url.path for request in self.requests], ["/simple/example-pkg/"]
        )

    async def test_ranges_python_markers_and_optional_scopes_do_not_filter(self):
        (self.root / "pyproject.toml").write_text(PROJECT)
        (self.root / "uv.lock").write_text(LOCK)
        inventory = read_dependency_inventory(self.snapshot)
        default_response = self.default_response

        def requires_new_python(request):
            response = default_response(request)
            if "/pypi/" in request.url.path:
                body = response.json()
                body["info"]["requires_python"] = ">=3.99"
                return type(response)(
                    200, json=body, headers={"content-type": request.headers["accept"]}
                )
            return response

        self.handler = requires_new_python
        with (
            patch(
                "packaging.markers.Marker.evaluate",
                side_effect=AssertionError("No environment filtering"),
            ),
            patch(
                "packaging.specifiers.SpecifierSet.contains",
                side_effect=AssertionError("No range filtering"),
            ),
        ):
            decisions, _ = await self.pipeline(inventory, self.provider)
        self.assertEqual(decisions[0].request.target_version, "3.0")
        self.assertEqual(decisions[0].dependency.specifier, "<2")
        self.assertEqual(decisions[0].target_metadata.requires_python, ">=3.99")
        (self.root / "uv.lock").unlink()
        (self.root / "pyproject.toml").write_text("""[project]
name="project"
dependencies=["example-pkg==1.0"]
[project.optional-dependencies]
test=["Example_Pkg==1.5; sys_platform == 'win32'"]
""")
        inventory = read_dependency_inventory(self.snapshot)
        with (
            patch(
                "packaging.markers.Marker.evaluate",
                side_effect=AssertionError("No environment filtering"),
            ),
            patch(
                "packaging.specifiers.SpecifierSet.contains",
                side_effect=AssertionError("No range filtering"),
            ),
        ):
            decisions, _ = await self.pipeline(inventory, self.provider)
        self.assertEqual(len(decisions), 2)
        self.assertEqual(
            {item.dependency.scope for item in decisions}, {"runtime", "extra:test"}
        )
        self.assertEqual(
            {item.request.current_version for item in decisions}, {"1.0", "1.5"}
        )
        self.assertTrue(all(item.request.target_version == "3.0" for item in decisions))
        self.assertTrue(any(item.dependency.marker for item in decisions))

    async def test_selection_and_collection_preserve_files_inventory_and_database(self):
        engine = make_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            session.add(RepoModel(id=1, name="project", repo_url=self.repo.repo_url))
        async with sessions.begin() as session:
            session.add(
                DependencyModel(repo_id=1, name="old-package", current_version="1.0")
            )
            session.add(
                MonitoringRun(
                    repo_id=1, started_at=datetime(2026, 1, 1), state="running"
                )
            )
        (self.root / "requirements.txt").write_text("Example_Pkg==1.0\n")
        inventory = read_dependency_inventory(self.snapshot)
        before_inventory = asdict(inventory)
        before_files = {path.name: path.read_bytes() for path in self.root.iterdir()}
        default_response = self.default_response

        def metadata_only(request):
            self.assertEqual(request.url.host, "pypi.org")
            self.assertTrue(request.url.path.startswith(("/simple/", "/pypi/")))
            response = default_response(request)
            body = response.json()
            if "info" in body:
                body["info"]["project_urls"] = {
                    "Changelog": "https://example.org/changes"
                }
            else:
                body["files"] = [
                    {
                        "filename": "example_pkg-3.0.tar.gz",
                        "url": "https://files.pythonhosted.org/package.tar.gz",
                        "hashes": {},
                        "size": 1,
                    }
                ]
            return type(response)(
                200, json=body, headers={"content-type": request.headers["accept"]}
            )

        self.handler = metadata_only
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            side_effect=AssertionError("No database access"),
        ):
            decisions, materials = await self.pipeline(inventory, self.provider)
        self.assertEqual(decisions[0].status, "selected")
        self.assertEqual(len(materials), 1)
        self.assertEqual(asdict(inventory), before_inventory)
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.root.iterdir()}, before_files
        )
        self.assertFalse(self.client.is_closed)
        async with sessions() as session:
            dependency = (await session.scalars(select(DependencyModel))).one()
            run = (await session.scalars(select(MonitoringRun))).one()
            self.assertEqual(
                (dependency.name, dependency.current_version), ("old-package", "1.0")
            )
            self.assertEqual(run.state, "running")


class SelectionImportTests(unittest.TestCase):
    def test_cold_import_does_not_require_bot_or_model_credentials(self):
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("BOT_", "LLM_", "MODEL_", "LANGFUSE_"))
        }
        script = """
import sys
from lovogit.app.update_selection import select_latest_stable_update
forbidden = ("lovogit.bot", "lovogit.agent", "lovogit.db", "lovogit.sandbox", "lovogit.cfg.settings", "langchain")
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
