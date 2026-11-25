import asyncio
import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from lovogit.agent.comparer.agents import create_investigation_agent
from lovogit.agent.comparer.state import ComparerState
from lovogit.index.api import Toolkit


async def collect_context(
    state: ComparerState,
    *,
    model: BaseChatModel,
    toolkit: Toolkit,
    config: RunnableConfig | None = None,
    timeout: float = 120,
    executor=None,
    model_call_limit: int | None = None,
    tool_call_limit: int | None = None,
):
    agent = create_investigation_agent(
        model,
        toolkit,
        stage="compare",
        executor=executor,
        model_call_limit=model_call_limit,
        tool_call_limit=tool_call_limit,
    )
    document = {
        "repository_id": state["repository_id"],
        "repository_sha": state["repository_sha"],
        "change": state["change"],
    }
    async with asyncio.timeout(timeout):
        result = await agent.ainvoke(
            {"messages": [HumanMessage(json.dumps(document, ensure_ascii=False))]},
            config=config,
        )
    return {"context": result["structured_response"]}
