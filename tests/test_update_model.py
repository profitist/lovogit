import json
import unittest

import httpx

from lovogit.app.providers.llm.client import create_update_model
from lovogit.cfg.project_update import ProjectUpdateSettings, UpdateModelSettings


class UpdateModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_tools_and_configuration(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "chat",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "strong",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        settings = UpdateModelSettings(
            _env_file=None,
            LLM_API_KEY="test-secret",
            LLM_BASE_URL="https://model.test/v1",
            MODEL_CHEAP="cheap",
            MODEL_STRONG="strong",
            LLM_REASONING_EFFORT="",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            for role, expected in [("extraction", "cheap"), ("compare", "strong")]:
                model = create_update_model(
                    settings,
                    ProjectUpdateSettings(_env_file=None),
                    role=role,
                    client=client,
                    callbacks=[],
                )
                self.assertEqual(model.max_retries, 0)
                if role == "compare":
                    model = model.bind_tools(
                        [
                            {
                                "type": "function",
                                "function": {
                                    "name": "search",
                                    "description": "Search code",
                                    "parameters": {"type": "object", "properties": {}},
                                },
                            }
                        ]
                    )
                await model.ainvoke("hello")
                body = json.loads(requests[-1].content)
                self.assertEqual(body["model"], expected)
                self.assertNotIn("reasoning_effort", body)
            self.assertIn("tools", body)
            self.assertEqual(
                str(requests[-1].url), "https://model.test/v1/chat/completions"
            )

    def test_unsupported_provider(self):
        with self.assertRaises(ValueError):
            UpdateModelSettings(
                _env_file=None,
                LLM_PROVIDER="google_genai",
                LLM_API_KEY="key",
                LLM_BASE_URL="url",
                MODEL_CHEAP="a",
                MODEL_STRONG="b",
            )
