from typing import Literal

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from lovogit.agent.comparer.prompts import (
    ANALYZE_PROMPT,
    COMPARE_PROMPT,
    SOLUTION_PROMPT,
)
from lovogit.agent.comparer.state import ContextResult
from lovogit.agent.tools.repo_search import create_repo_search_tools
from lovogit.index.api import Toolkit

_PROMPTS = {
    "compare": COMPARE_PROMPT,
    "analyze": ANALYZE_PROMPT,
    "solution": SOLUTION_PROMPT,
}


def create_investigation_agent(
    model: BaseChatModel,
    toolkit: Toolkit,
    *,
    stage: Literal["compare", "analyze", "solution"] = "compare",
    executor=None,
    model_call_limit: int | None = None,
    tool_call_limit: int | None = None,
):
    if stage not in _PROMPTS:
        raise ValueError(f"Cannot create investigation agent: unknown stage {stage!r}")
    return create_agent(
        model=model,
        tools=create_repo_search_tools(toolkit, executor=executor),
        middleware=[
            *(
                [
                    ModelCallLimitMiddleware(
                        run_limit=model_call_limit, exit_behavior="error"
                    )
                ]
                if model_call_limit
                else []
            ),
            *(
                [
                    ToolCallLimitMiddleware(
                        run_limit=tool_call_limit, exit_behavior="error"
                    )
                ]
                if tool_call_limit
                else []
            ),
        ],
        system_prompt=_PROMPTS[stage],
        response_format=(
            ToolStrategy(ContextResult, handle_errors=False)
            if stage == "compare"
            else None
        ),
        name=f"update_{stage}",
    )
