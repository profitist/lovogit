import asyncio
import json
import tempfile
import threading
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from test_comparer_agents import ToolCallingModel
from test_pypi_provider import metadata
from test_source_document import Body

from lovogit.app.project_update.code_evidence import verify_location
from lovogit.app.project_update.dto import ProjectUpdateRequest
from lovogit.app.project_update.tracing import RunTrace, TraceCallbacks
from lovogit.app.project_update.workflow import analyze_project_update
from lovogit.app.providers.github.dto import ResolvedRepoDTO, SourceSnapshotDTO
from lovogit.app.providers.llm.client import UpdateModelConfigurationError
from lovogit.app.providers.pypi.client import PyPiProvider
from lovogit.app.providers.source_documents.loader import SourceDocumentLoader
from lovogit.cfg.project_update import ProjectUpdateSettings
from lovogit.cfg.pypi import PyPiSettings
from lovogit.cfg.source_fetch import SourceFetchSettings
from lovogit.index.worker import open_index


class SnapshotFixture:
    def __init__(self, declaration="Example_Pkg==1.0\n"):
        self.declaration = declaration
        self.root = None
        self.pyproject = None
        self.repo = ResolvedRepoDTO(
            123, "team/project", "https://github.com/team/project", "main", "a" * 40
        )

    @asynccontextmanager
    async def open_source(self, url):
        with tempfile.TemporaryDirectory() as directory:
            self.root = Path(directory)
            if self.declaration is not None:
                (self.root / "requirements.txt").write_text(self.declaration)
            if self.pyproject is not None:
                (self.root / "pyproject.toml").write_text(self.pyproject)
            (self.root / "example.py").write_text(
                "import example_pkg\ndef example():\n    return example_pkg.old()\n"
            )
            yield SourceSnapshotDTO(self.repo, self.root)


class ProjectWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.repos = SnapshotFixture()
        self.versions = ["1.0", "2.0"]
        self.http_calls, self.roles = [], []
        self.failure = None
        self.two_sources = False
        self.extra_changes = 0
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.pypi_response)
        )
        self.addAsyncCleanup(self.client.aclose)
        self.pypi = PyPiProvider(self.client, PyPiSettings(_env_file=None))
        self.limits = ProjectUpdateSettings(_env_file=None)
        self.trace = RunTrace(self.folder, self.limits)
        self.callbacks = [TraceCallbacks(self.trace)]
        self.record = {
            "category": "default_value",
            "description": "remove old",
            "symbol": None,
            "release_version": "2.0",
            "evidence": [{"source_id": "source-0", "quote": "remove old"}],
            "version_evidence": {"source_id": "source-0", "quote": "2.0"},
        }
        self.context = {
            "usages": [
                {
                    "file": "example.py",
                    "line": 3,
                    "symbol": "example",
                    "reason": "uses removed behavior",
                }
            ],
            "related_tests": [],
            "analysis_complete": True,
        }
        self.compare_model = None

    def pypi_response(self, request):
        self.http_calls.append(request.url.path)
        if self.failure:
            return httpx.Response(self.failure)
        if request.url.path.startswith("/simple/"):
            return httpx.Response(
                200,
                json={
                    "meta": {"api-version": "1.4"},
                    "name": "example-pkg",
                    "versions": self.versions,
                    "files": [],
                },
                headers={"content-type": request.headers["accept"]},
            )
        body = metadata()
        body["info"]["version"] = request.url.path.split("/")[-2]
        if self.two_sources:
            body["info"]["project_urls"]["Changelog"] += "/" + body["info"]["version"]
        return httpx.Response(200, json=body)

    def models(self, role):
        self.roles.append(role)
        if role == "extraction":
            return FakeMessagesListChatModel(
                callbacks=self.callbacks,
                responses=[
                    AIMessage(
                        json.dumps(
                            {
                                "changes": [self.record] * (1 + self.extra_changes),
                                "limitations": [],
                            }
                        )
                    ),
                    AIMessage("invalid"),
                ],
            )
        self.compare_model = ToolCallingModel(
            callbacks=self.callbacks,
            responses=[
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "search_text",
                            "args": {"query": "example"},
                            "id": "search",
                        }
                    ],
                ),
                AIMessage(
                    "",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "example.py"},
                            "id": "read",
                        }
                    ],
                ),
                AIMessage(
                    "",
                    tool_calls=[
                        {"name": "ContextResult", "args": self.context, "id": "context"}
                    ],
                ),
            ],
        )
        return self.compare_model

    def loader(self):
        return SourceDocumentLoader(
            SourceFetchSettings(_env_file=None),
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    stream=Body(b"2.0 remove old"),
                    headers={"content-type": "text/plain"},
                )
            ),
        )

    async def run_analysis(self, **kwargs):
        return await analyze_project_update(
            ProjectUpdateRequest("https://github.com/team/project", "Example.Pkg"),
            folder=self.folder,
            repos=self.repos,
            pypi=self.pypi,
            model_factory=self.models,
            loader_factory=self.loader,
            limits=self.limits,
            trace=self.trace,
            **kwargs,
        )

    async def test_full_pipeline_uses_actual_agent_index_and_preserves_evidence(self):
        result = await self.run_analysis()
        self.assertEqual(result.status, "completed", result.reason)
        self.assertEqual(result.repo.commit_sha, "a" * 40)
        self.assertFalse(self.repos.root.exists())
        self.assertEqual(result.selection.request.current_version, "1.0")
        self.assertEqual(result.selection.request.target_version, "2.0")
        location = result.searches[0].locations[0]
        self.assertTrue(location["verified"])
        self.assertIn("example_pkg.old()", location["snippet"])
        self.assertIn("/blob/" + "a" * 40, location["permalink"])
        self.assertEqual(result.searches[0].change_id, "change-0")
        self.assertTrue(
            any(
                isinstance(message, ToolMessage)
                and "example_pkg.old" in str(message.content)
                for message in self.compare_model.seen_messages[-1]
            )
        )
        raw = json.loads((self.folder / "trace.json").read_text())
        self.assertEqual(sum(item["kind"] == "generation" for item in raw["spans"]), 4)
        self.assertEqual(sum(item["kind"] == "tool" for item in raw["spans"]), 2)
        self.assertTrue(
            all(item["parent_id"] for item in raw["spans"] if item["kind"] != "span")
        )
        page = (self.folder / "report.html").read_text()
        self.assertIn("<mark><u>remove old</u></mark>", page)
        self.assertIn("Сырые ответы модели", page)
        self.assertNotIn("lovogit.cfg.settings", __import__("sys").modules)

    async def test_no_update_without_model_or_index(self):
        self.versions = ["1.0"]
        with patch(
            "lovogit.app.project_update.workflow.open_index",
            side_effect=AssertionError("index must not run"),
        ):
            result = await self.run_analysis()
        self.assertEqual(result.status, "no_update")
        self.assertEqual(self.roles, [])
        self.assertEqual(self.http_calls, ["/simple/example-pkg/"])

    async def test_configuration_error_is_actionable_in_report(self):
        def invalid_model(role):
            raise UpdateModelConfigurationError(["LLM_PROVIDER"])

        self.models = invalid_model
        result = await self.run_analysis()
        self.assertEqual(result.status, "failed")
        self.assertIn("LLM_PROVIDER", "\n".join(result.diagnostics))
        self.assertIn("openai_compat", (self.folder / "report.html").read_text())

    async def test_inventory_early_exits_without_pypi(self):
        for declaration, package, expected in [
            (None, "Example.Pkg", "no_sources"),
            ("other==1.0\n", "Example.Pkg", "dependency_not_found"),
            (
                'Example_Pkg==1.0; sys_platform == "win32"\nExample_Pkg==2.0; sys_platform == "linux"\n',
                "Example.Pkg",
                "ambiguous_dependency",
            ),
        ]:
            with self.subTest(expected=expected):
                self.repos = SnapshotFixture(declaration)
                result = await self.run_analysis()
                self.assertEqual(result.status, "skipped")
                self.assertEqual(result.reason, expected)
                self.assertEqual(self.http_calls, [])
        self.repos = SnapshotFixture("Example_Pkg>=1\n")
        self.assertEqual(
            (await self.run_analysis()).selection.reason, "unknown_current"
        )

    async def test_pypi_errors_do_not_become_no_update(self):
        self.failure = 429
        result = await self.run_analysis()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.stages["selection"].outcome, "failed")
        self.assertEqual(self.roles, [])

    async def test_failed_and_partial_inventory_are_distinguished(self):
        self.repos.pyproject = "invalid = ["
        result = await self.run_analysis()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.inventory.status, "failed")
        self.assertFalse(self.http_calls)
        self.repos.pyproject = None
        self.repos.declaration += "--unsupported-directive x\n"
        result = await self.run_analysis()
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.inventory.status, "partial")

    async def test_cancellation_preserves_report_and_cleans_snapshot(self):
        reached = asyncio.Event()

        async def waiting_selection(*args, **kwargs):
            reached.set()
            await asyncio.Event().wait()

        with patch(
            "lovogit.app.project_update.workflow.select_latest_stable_update",
            side_effect=waiting_selection,
        ):
            task = asyncio.create_task(self.run_analysis())
            await reached.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.repos.root.exists())
        saved = json.loads((self.folder / "report.json").read_text())
        self.assertEqual(saved["reason"], "cancelled")
        self.assertNotEqual(saved["status"], "completed")
        self.assertEqual(self.roles, [])

    async def test_no_materials_skips_model_and_index(self):
        self.loader = lambda: SourceDocumentLoader(
            SourceFetchSettings(_env_file=None),
            httpx.MockTransport(lambda request: httpx.Response(404)),
        )
        result = await self.run_analysis()
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.extraction.status, "no_materials")
        self.assertEqual(self.roles, [])

    async def test_unscoped_and_rejected_are_not_searched(self):
        self.record["release_version"] = None
        self.record["version_evidence"] = None
        result = await self.run_analysis()
        self.assertEqual(result.status, "partial")
        self.assertEqual(self.roles, ["extraction"])
        self.assertEqual(len(result.extraction.unscoped_changes), 1)
        self.record["evidence"][0]["quote"] = "invented"
        result = await self.run_analysis()
        self.assertTrue(result.extraction.rejected)
        self.assertFalse(result.searches)

    async def test_model_limit_preserves_extraction(self):
        self.limits = ProjectUpdateSettings(_env_file=None, UPDATE_MODEL_CALLS=1)
        result = await self.run_analysis()
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.searches[0].outcome, "failed")
        self.assertEqual(len(self.compare_model.seen_messages), 1)
        self.assertTrue(result.extraction.changes)

    async def test_partial_extraction_preserves_successful_document(self):
        self.two_sources = True
        result = await self.run_analysis()
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.extraction.status, "partial")
        self.assertEqual(len(result.extraction.changes), 1)
        self.assertEqual(result.searches[0].outcome, "completed")

    async def test_change_limit_lists_unprocessed_changes(self):
        self.extra_changes = 1
        self.limits = ProjectUpdateSettings(_env_file=None, UPDATE_MAX_CHANGES=1)
        result = await self.run_analysis()
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.extraction.changes), 2)
        self.assertEqual(result.searches[1].reason, "change_limit")
        self.assertEqual(self.roles, ["extraction", "compare"])

    async def test_tool_limit_prevents_additional_execution(self):
        self.limits = ProjectUpdateSettings(_env_file=None, UPDATE_TOOL_CALLS=1)
        result = await self.run_analysis()
        self.assertEqual(result.status, "partial")
        trace = json.loads((self.folder / "trace.json").read_text())
        self.assertEqual(
            [item["name"] for item in trace["spans"] if item["kind"] == "tool"],
            ["search_text"],
        )

    async def test_deadline_stops_new_calls(self):
        self.limits = ProjectUpdateSettings(_env_file=None, UPDATE_DEADLINE=0.001)

        async def slow_selection(*args, **kwargs):
            await asyncio.sleep(1)

        with patch(
            "lovogit.app.project_update.workflow.select_latest_stable_update",
            side_effect=slow_selection,
        ):
            result = await self.run_analysis()
        self.assertIn(result.status, {"failed", "partial"})
        self.assertEqual(self.roles, [])
        self.assertFalse(self.repos.root.exists())

    async def test_code_references_are_checked(self):
        async with self.repos.open_source("url") as snapshot:
            for path, line in [
                ("../outside", 1),
                ("example.py", 99),
                ("missing.py", 1),
            ]:
                self.assertFalse(
                    verify_location(
                        {"file": path, "line": line}, snapshot, self.limits
                    )["verified"]
                )
            self.assertTrue(
                verify_location(
                    {"file": "example.py", "line": 3}, snapshot, self.limits
                )["verified"]
            )

    async def test_cancelled_build_waits_and_closes_before_snapshot_cleanup(self):
        started, finished = threading.Event(), threading.Event()
        from lovogit.index.builder import RepoIndex

        original = RepoIndex.build

        def slow_build(index):
            started.set()
            time.sleep(0.03)
            original(index)
            finished.set()

        async with self.repos.open_source("url") as snapshot:
            with patch.object(RepoIndex, "build", slow_build):

                async def build():
                    async with open_index(
                        snapshot.root, self.folder / "cancelled.sqlite"
                    ):
                        self.fail("cancelled build must not yield")

                task = asyncio.create_task(build())
                while not started.is_set():
                    await asyncio.sleep(0.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(finished.is_set())
                self.assertTrue(snapshot.root.exists())
