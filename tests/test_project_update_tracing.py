import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from lovogit.app.project_update.trace_export import export_trace
from lovogit.app.project_update.tracing import (
    RunTrace,
    TraceBudgetExceeded,
    TraceCallbacks,
)
from lovogit.cfg.project_update import ProjectUpdateSettings, TraceExportSettings


class TracingTests(unittest.IsolatedAsyncioTestCase):
    async def test_callbacks_redaction_parentage_and_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            trace = RunTrace(
                folder, ProjectUpdateSettings(_env_file=None), secrets=("secret-key",)
            )
            callback = TraceCallbacks(trace)
            with trace.observe("extraction"):
                run_id = uuid4()
                callback.on_chat_model_start(
                    {}, [[AIMessage("secret-key")]], run_id=run_id
                )
                callback.on_llm_end(
                    LLMResult(
                        generations=[
                            [
                                ChatGeneration(
                                    message=AIMessage(
                                        "answer",
                                        usage_metadata={
                                            "input_tokens": 2,
                                            "output_tokens": 3,
                                            "total_tokens": 5,
                                        },
                                    )
                                )
                            ]
                        ]
                    ),
                    run_id=run_id,
                )
                tool_id = uuid4()
                callback.on_tool_start(
                    {"name": "search"}, "query", run_id=tool_id, parent_run_id=run_id
                )
                callback.on_tool_error(ValueError("secret-key"), run_id=tool_id)
            text = (folder / "trace.json").read_text()
            self.assertNotIn("secret-key", text)
            data = json.loads(text)
            self.assertEqual(len(data["spans"]), 3)
            self.assertEqual(data["spans"][1]["parent_id"], data["spans"][0]["id"])
            self.assertEqual(data["spans"][2]["parent_id"], data["spans"][1]["id"])
            self.assertEqual(data["spans"][1]["usage"]["total"], 5)
            self.assertTrue((folder / "langfuse-otel.json").exists())
            self.assertFalse(list(folder.glob("*.tmp")))

    async def test_budget_and_tool_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            trace = RunTrace(
                folder, ProjectUpdateSettings(_env_file=None, UPDATE_TOOL_CHARS=5)
            )
            trace.record("start", "tool", name="read", kind="tool", input="x" * 100)
            trace.record("end", "tool", output="value")
            with self.assertRaises(TraceBudgetExceeded):
                trace.check_budget()
            value = json.loads((folder / "trace.json").read_text())["spans"][0]["input"]
            self.assertTrue(value["truncated"])
            self.assertEqual(value["original_chars"], 102)
            self.assertLessEqual(len(value["text"]), 5)

    async def test_export_outcomes_and_duplicate_protection(self):
        settings = TraceExportSettings(
            _env_file=None,
            LANGFUSE_HOST="https://trace.test",
            LANGFUSE_PUBLIC_KEY="public",
            LANGFUSE_SECRET_KEY="secret",
        )
        for response, expected in [
            (httpx.Response(200, json={}), "accepted"),
            (
                httpx.Response(200, json={"partialSuccess": {"rejectedSpans": 1}}),
                "partial",
            ),
            (httpx.Response(401), "failed"),
            (httpx.Response(500), "unknown"),
        ]:
            with (
                self.subTest(expected=expected),
                tempfile.TemporaryDirectory() as directory,
            ):
                folder = Path(directory)
                trace = RunTrace(folder, ProjectUpdateSettings(_env_file=None))
                with trace.observe("test"):
                    pass
                calls = []

                def handle(request, calls=calls, response=response):
                    calls.append(request)
                    return response

                transport = httpx.MockTransport(handle)
                result = await export_trace(folder, settings, transport=transport)
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(result["payload_sha256"]), 64)
                if expected != "failed":
                    with self.assertRaises(ValueError):
                        await export_trace(folder, settings, transport=transport)
                    self.assertEqual(len(calls), 1)
                else:
                    await export_trace(folder, settings, transport=transport)
                    self.assertEqual(len(calls), 2)
