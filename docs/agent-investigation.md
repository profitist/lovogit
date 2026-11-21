# Исследование влияния обновления на код

Промпты находятся в `lovogit.agent.comparer.prompts`. Выбор модели не определяет
роль агента: одну модель можно использовать для всех этапов или передать разные.
Прежние неиспользуемые `agent/cheap/prompts.py` и заготовки `agent/core.py` удалены.

`create_investigation_agent(model, toolkit, stage=...)` создаёт запускаемого агента:

- `compare` — ищет затронутый код и связанные тесты; возвращает `ContextResult`.
- `analyze` — проверяет последствия обновления; возвращает текстовый анализ.
- `solution` — предлагает изменения и способы проверки; возвращает текст.

Все три роли получают инструменты чтения из `create_repo_search_tools`.
Модель должна поддерживать вызов инструментов. Для `compare` результат тоже
возвращается через инструмент со схемой; некорректная структура вызывает ошибку.
Глобальные настройки и credentials при импорте не загружаются.

## Пример

Передайте готовую chat model и Toolkit, привязанный к индексу нужного снимка
репозитория. Ни агент, ни сборщик контекста не строят индекс самостоятельно.

```python
import json

from langchain_core.messages import HumanMessage

from lovogit.agent.comparer.agents import create_investigation_agent
from lovogit.agent.comparer.context import collect_context


async def investigate(model, toolkit, repository_id, repository_sha, change):
    state = {
        "repository_id": repository_id,
        "repository_sha": repository_sha,
        "change": change,
    }
    config = {"recursion_limit": 25}
    context = await collect_context(
        state, model=model, toolkit=toolkit, config=config, timeout=120
    )
    analyzer = create_investigation_agent(model, toolkit, stage="analyze")
    assessment = await analyzer.ainvoke(
        {"messages": [HumanMessage(json.dumps({**state, **context}))]},
        config=config,
    )
    return {**context, "assessment": assessment["messages"][-1].content}
```

`change` содержит `package`, `current_version`, `target_version`,
`changed_api` и `change_description`. Для запроса предложений создайте агента
с `stage="solution"` и передайте ему изменение, найденный контекст и анализ.
Вызовы агентов можно оборачивать в `asyncio.timeout`; сборщик контекста делает
это сам. `config` передаётся LangGraph, включая callbacks, metadata и лимит шагов.
Для узла графа зависимости можно привязать через
`functools.partial(collect_context, model=model, toolkit=toolkit)`.

`analysis_complete` описывает полноту поиска со слов модели. Это не признак
совместимости. Координаты кода и выводы требуют проверки: структурированная схема
проверяет форму ответа, но не доказывает его достоверность. Поиск работает только
с переданным индексом; API целевой версии нужно предоставить в материалах, если
его нет в репозитории. Инструментов изменения файлов и выполнения тестов нет.

Это отдельные запускаемые этапы исследования. Полный граф
`agent/comparer/graph.py` с генерацией и выполнением тестов остаётся заготовкой.
Извлечение изменений из changelog в `agent/update_sources` остаётся отдельным
этапом и использует свой промпт без инструментов поиска.

Проверки без сети и платных вызовов модели:

```sh
PYTHONPATH=src:tests .venv/bin/python -m unittest test_comparer_agents
```
