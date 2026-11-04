# Tasks

## 1. Чтение сохранённых результатов

- [x] 1.1 Добавить `AttentionService`, DTO и `AttentionUnavailable` по дизайну; в `tests/test_attention_service.py` проверить три выбранных статуса, сортировку проектов и зависимостей, одинаковые пакеты разных проектов и исключение удалённых проектов.
- [x] 1.2 Проверить в сервисных тестах отсутствие снимка, пустой снимок, снимок без выбранных статусов, новую ошибку и выполняющийся запуск, обратный порядок завершения и равное время начала; подтвердить UTC и один SQL-запрос независимо от числа проектов.
- [x] 1.3 Добавить проверку преобразования ошибки БД с сохранением причины и отсутствия изменений истории после чтения; выполнить `PYTHONPATH=src:tests uv run python -m unittest discover -s tests -p 'test_attention_service.py'`.

## 2. Текстовый ответ

- [x] 2.1 Добавить `attention_format.py` с переиспользованием существующих названий статусов, UTC и splitter; тестами проверить все поля, неизвестную последнюю версию, состояния без оценок и без выбранных зависимостей, предупреждение об устаревании и текст ограничения анализа.
- [x] 2.2 В `tests/test_attention_format.py` проверить длинный ответ с emoji и символами HTML/Markdown: полное восстановление текста, порядок и максимум 4096 единиц UTF-16 на сообщение; выполнить целевые тесты форматирования.

## 3. Защищённая команда

- [x] 3.1 Добавить middleware с проверкой чата до открытия сессии и отдельной сессией на запрос; тестами проверить запрет при пустом allowlist, одинаковый доступ участников, конкурентные запросы и закрытие сессии при исключении.
- [x] 3.2 Добавить обработчик и роутер `/attention`, удалить его заглушку из `commands.py`, подключить роутер в `bot.py`; тестом полного Dispatcher с общим роутером проверить попадание разрешённых и неразрешённых команд в новый защищённый обработчик.
- [x] 3.3 Покрыть тестами обработчика нейтральную ошибку чтения с логированием, отправку всех частей с `parse_mode=None` и распространение ошибки отправки Telegram; убедиться, что обработчик вызывает только сервис чтения.
- [x] 3.4 Обновить описание `/attention` и доступа в `docs/dev-docs.md`, текущее состояние в `docs/mvp.md` и текст `/help`: сохранённые результаты всех проектов, без запуска проверки; кнопка и ссылка остаются будущим этапом. Проверить соответствие документации спецификации, не меняя агентские разделы.

## 4. Общая проверка

- [x] 4.1 Выполнить `PYTHONPATH=src:tests uv run python -m unittest discover -s tests` и `uv run ruff check src tests`; подтвердить сохранение поведения `/status`, отсутствие новых миграций и зависимостей и отсутствие изменений агентской части по итоговому diff.

## Verification notes

- 73 tests passed with `PYTHONPATH=src:tests /private/tmp/lovogit-attention-verify/bin/python -m unittest discover -s tests` on standard CPython 3.14.0 using the project's locked dependencies. The working `.venv` was not changed.
- The initial full run on local free-threaded CPython 3.14.4 had four pre-existing Alembic subprocess timeouts, consistent with the environment limitation documented in `docs/dev-docs.md`; enabling the GIL did not resolve migration timeouts.
- `ruff check src tests`, `openspec validate add-bot-attention --strict`, and `git diff --check` passed.
- No agent code, source providers, migrations, project dependencies, or unrelated `output/` artifacts were changed.
