import json
import subprocess
import sys
import unittest
from unittest.mock import Mock

from langchain.agents.structured_output import StructuredOutputValidationError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from pydantic import Field

from lovogit.agent.comparer.agents import create_investigation_agent
from lovogit.agent.comparer.context import collect_context
from lovogit.index.api import Toolkit
from lovogit.index.builder import RepoIndex


class ToolCallingModel(FakeMessagesListChatModel):
    seen_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen_messages.append(messages)
        return super()._generate(messages, *args, **kwargs)


class ComparerAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.toolkit = Toolkit(Mock(spec=RepoIndex, tree="src/example.py"))
        self.state = {
            "repository_id": "example",
            "repository_sha": "a" * 40,
            "change": {
                "package": "httpx",
                "current_version": "0.27.2",
                "target_version": "0.28.0",
                "changed_api": "Client",
                "change_description": "The proxies argument was removed",
            },
        }

    async def test_context_executes_repository_tool_and_returns_structured_result(self):
        context = {"usages": [], "related_tests": [], "analysis_complete": False}
        model = ToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_tree", "args": {}, "id": "tree"}],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "ContextResult", "args": context, "id": "context"}
                    ],
                ),
            ]
        )
        result = await collect_context(self.state, model=model, toolkit=self.toolkit)
        self.assertEqual(result, {"context": context})
        messages = model.seen_messages[-1]
        self.assertIn("may be affected", messages[0].content)
        self.assertEqual(
            json.loads(messages[1].content)["change"], self.state["change"]
        )
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and message.content == "src/example.py"
                for message in messages
            )
        )
        self.assertNotIn("context", self.state)

    async def test_other_stages_use_their_prompts_and_repository_tools(self):
        for stage, instruction in (
            ("analyze", "determine the consequences"),
            ("solution", "smallest sufficient changes"),
        ):
            with self.subTest(stage=stage):
                model = ToolCallingModel(
                    responses=[
                        AIMessage(
                            content="",
                            tool_calls=[{"name": "get_tree", "args": {}, "id": "tree"}],
                        ),
                        AIMessage(content="Insufficient evidence"),
                    ]
                )
                agent = create_investigation_agent(model, self.toolkit, stage=stage)
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": "Investigate update"}]}
                )
                self.assertEqual(
                    result["messages"][-1].content, "Insufficient evidence"
                )
                self.assertIn(instruction, model.seen_messages[0][0].content)
                self.assertTrue(
                    any(
                        isinstance(message, ToolMessage)
                        for message in result["messages"]
                    )
                )

    async def test_invalid_context_does_not_silently_become_complete(self):
        model = ToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "ContextResult", "args": {}, "id": "invalid"}],
                )
            ]
        )
        with self.assertRaises(StructuredOutputValidationError):
            await collect_context(self.state, model=model, toolkit=self.toolkit)

    async def test_repeated_searches_stop_at_the_supplied_recursion_limit(self):
        model = ToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_tree", "args": {}, "id": "tree"}],
                )
            ]
        )
        with self.assertRaises(GraphRecursionError):
            await collect_context(
                self.state,
                model=model,
                toolkit=self.toolkit,
                config={"recursion_limit": 4},
            )

    def test_unknown_stage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown stage"):
            create_investigation_agent(Mock(), self.toolkit, stage="unknown")

    def test_import_does_not_require_global_credentials(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import lovogit.agent.comparer.agents; import sys; "
                    "assert 'lovogit.cfg.settings' not in sys.modules"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
