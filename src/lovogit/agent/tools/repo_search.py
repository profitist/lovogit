from langchain.tools import tool
from langchain_core.tools import BaseTool

from lovogit.index.api import Toolkit
from lovogit.index.worker import run_index_operation


def create_repo_search_tools(toolkit: Toolkit, *, executor=None) -> list[BaseTool]:
    """Обернуть операции Toolkit в инструменты LangChain для поиска по репозиторию.

    Args:
        toolkit: Инструментарий, созданный с RepoIndex нужного репозитория.

    Returns:
        Инструменты с именами, описаниями и схемами аргументов методов Toolkit.
        Каждый инструмент сохраняет привязку к переданному экземпляру.
    """
    return [
        _search_tool(method, executor)
        for method in (
            toolkit.search_symbols,
            toolkit.search,
            toolkit.search_page,
            toolkit.best_symbol,
            toolkit.symbol_context,
            toolkit.references,
            toolkit.implementations,
            toolkit.callers,
            toolkit.callees,
            toolkit.inspect_symbol,
            toolkit.inspect_text,
            toolkit.search_text,
            toolkit.outline,
            toolkit.outline_text,
            toolkit.read_file,
            toolkit.get_tree,
        )
    ]


def _search_tool(method, executor):
    result = tool(method)
    if executor is not None:

        async def invoke(*args, **kwargs):
            return await run_index_operation(executor, method, *args, **kwargs)

        result.coroutine = invoke
    return result
