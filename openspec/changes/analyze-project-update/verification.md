# Verification

Проверено 2026-11-21, после реализации связки и исправления владения SQLite-потоком.

- `PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests`: 313 тестов, 8 skipped, ошибок нет.
- Ruff check и format для новых модулей, затронутых agent/tool модулей и тестов: успешно.
- `openspec validate analyze-project-update --strict`: успешно.
- `git diff --check`: успешно.
- CLI help для `lovogit.cli.analyze_update` и `lovogit.cli.export_trace`: успешно.
- Импорт CLI без глобальных bot settings, полного comparer graph и импортов из artifacts: успешно.
- `git check-ignore` для сохранённого fixture-отчёта: каталог игнорируется.

Offline integration проверяет настоящий индекс и compare agent с fake tool-calling моделью, PyPI MockTransport, ограниченный loader и временный снимок. Покрыты no_update без LLM/индекса, неоднозначные декларации, failed/partial inventory, ошибки PyPI, no_materials, unscoped/rejected, частичная extraction, лимиты changes/model/tools, timeout и отмена. Проверены сырой трейс, родительские связи, сохранённые цитаты и фрагменты после удаления снимка. Проектный код и тесты не исполняются; мониторинг и БД не подключаются.

Пример без ключей и платных запросов сохранён локально:
`artifacts/runs/20261121T065539Z-example-pkg-5vjoy09p/report.html`.

## Remaining validation

Задача 4.2 частично выполнена и намеренно не отмечена завершённой: renderer и автоматические проверки готовы, визуальный осмотр страницы не выполнен. Встроенный browser host недоступен; попытка просмотра через native Firefox завершилась отказом инструмента: `Computer Use was not approved to use Firefox`. Необходим просмотр HTML в браузере; автоматические проверки не заменяют этот шаг.

Реальная Polza-сессия и отправка в Langfuse не запускались; интеграции проверены offline, это соответствует плану проверки без платного smoke run.
