from typing import Literal

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI

from lovogit.cfg.project_update import ProjectUpdateSettings, UpdateModelSettings


class UpdateModelConfigurationError(ValueError):
    def __init__(self, fields):
        self.reason = f"Cannot configure manual LLM: {', '.join(fields)}; only openai_compat is supported"
        super().__init__(self.reason)


def create_update_model(
    settings: UpdateModelSettings,
    limits: ProjectUpdateSettings,
    *,
    role: Literal["extraction", "compare"],
    client: httpx.AsyncClient,
    callbacks: list[BaseCallbackHandler],
) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.MODEL_CHEAP if role == "extraction" else settings.MODEL_STRONG,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        reasoning_effort=settings.LLM_REASONING_EFFORT or None,
        max_tokens=limits.UPDATE_MAX_TOKENS,
        timeout=limits.UPDATE_COMPARE_TIMEOUT,
        max_retries=0,
        http_async_client=client,
        callbacks=callbacks,
    )
