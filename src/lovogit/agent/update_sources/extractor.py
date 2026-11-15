import asyncio
import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from lovogit.agent.update_sources.prompts import SYSTEM_PROMPT
from lovogit.agent.update_sources.schemas import ExtractionResponseSchema
from lovogit.cfg.source_analysis import SourceAnalysisSettings


class SourceAnalysisError(Exception):
    def __init__(self, source_id: str, reason: str):
        self.reason = reason
        super().__init__(f"Cannot analyze source {source_id}: {reason}")


class UpdateChangeExtractor:
    def __init__(self, model, settings: SourceAnalysisSettings):
        self._model = model
        self._settings = settings

    async def extract(self, document: dict) -> ExtractionResponseSchema:
        source_id = document["source_id"]
        messages = [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(json.dumps(document, ensure_ascii=False)),
        ]
        try:
            async with asyncio.timeout(self._settings.SOURCE_ANALYSIS_CALL_TIMEOUT):
                response = await self._model.ainvoke(messages)
        except TimeoutError:
            raise SourceAnalysisError(source_id, "timeout") from None
        except Exception:  # noqa: BLE001
            raise SourceAnalysisError(source_id, "model_error") from None
        if response.additional_kwargs.get("refusal") or response.tool_calls:
            raise SourceAnalysisError(source_id, "refused")
        if not isinstance(response.content, str):
            raise SourceAnalysisError(source_id, "invalid_response")
        if len(response.content) > self._settings.SOURCE_ANALYSIS_MAX_RESPONSE_CHARS:
            raise SourceAnalysisError(source_id, "response_limit")
        try:
            return ExtractionResponseSchema.model_validate_json(response.content)
        except ValidationError:
            raise SourceAnalysisError(source_id, "invalid_response") from None
