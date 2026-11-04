# Tasks

## 1. Стабильная идентичность репозитория

- [x] 1.1 Добавить nullable `github_id` в `Repo` и Alembic-миграцию `0003_repository_identity` с уникальным индексом; тестами модели проверить запрет повторного GitHub ID, включая удалённую запись, и допустимость нескольких NULL.
- [x] 1.2 Дополнить миграционные тесты upgrade/downgrade на существующих проектах с зависимостями и историей: прежние ID и строки сохраняются, сеть не используется; описать порядок обновления и отката в разделе БД `docs/dev-docs.md` и выполнить тесты миграций на обычном Python 3.14.

## 2. Сервис регистрации

- [x] 2.1 Добавить `RepositoryRegistrationService` и DTO исхода операции: URL-проверка, `GitHubProvider.resolve`, затем короткая транзакция сохранения; тестами с подменённым провайдером проверить имя, канонический URL, GitHub ID, commit и сохранение после повторного открытия БД, отсутствие вызова snapshot и записей мониторинга.
- [x] 2.2 Реализовать повтор, актуализацию имени по GitHub ID, привязку единственной прежней записи без ID и восстановление удалённой; тестами проверить прежний локальный ID, сохранение зависимостей и истории, варианты регистра, `.git` и `/`, конфликт нескольких старых записей и противоречивых идентичностей без изменения данных.
- [x] 2.3 Реализовать транзакционную защиту конкурентного добавления и преобразование ошибок хранения; тестами на файловой SQLite с разными сессиями проверить единственную запись и одинаковые ID одновременных запросов, rollback при ошибке и отсутствие открытой write-транзакции во время сети.
- [x] 2.4 Проверить тестами все ошибки resolve, включая отсутствие ревизии, лимит и timeout: нет записи или изменения проекта; описать сервис и ограничение пустых репозиториев в `docs/dev-docs.md`, выполнить целевой набор сервисных тестов.

## 3. Команда и подключение бота

- [x] 3.1 Добавить защищённый роутер `/add_repo` и middleware, удалить старую заглушку; тестами Dispatcher с общим и действующими роутерами проверить регистрацию, запрет при пустом allowlist и неразрешённом чате без БД и сети, единые права участников разрешённого чата, подсказку для отсутствующих и лишних аргументов и отклонение неверного URL до сети.
- [x] 3.2 Добавить ответы для added/already_tracked/restored, безопасных ошибок GitHub, известного retry_at, конфликта старых записей и ошибки БД; тестами проверить `parse_mode=None`, отсутствие секретов и SQL в ответах и сохранение регистрации при ошибке отправки Telegram.
- [x] 3.3 Подключить фабрику регистрации и общий HTTP-клиент в `bot.py` через `GitHubSettings`; тестами жизненного цикла проверить отсутствие глобальной загрузки настроек при импорте, ранний отказ при неверной конфигурации и закрытие клиента при завершении и ошибке запуска, не изменяя контракт `RepoService`.
- [x] 3.4 Обновить `/help`, подсказки пустого списка `/status` и `/attention` и их тесты: рекомендовать `/add_repo <github-url>`; согласовать `docs/dev-docs.md`, `docs/frontend.md`, `docs/mvp.md` и `docs/backlog.md` с добавлением через бота, общим списком и отсутствием автоматического анализа, сохранив агентские разделы.

## 4. Интеграционная проверка

- [x] 4.1 На мигрированной БД провести интеграционный тест с подменённым GitHub HTTP: `/add_repo`, повтор, `/status`, `/attention`, повторное открытие БД; подтвердить одну запись и отсутствие запусков или результатов, а также сохранность истории восстановленного проекта.
- [x] 4.2 Выполнить полный Python unittest-набор и `ruff check src tests`, строгую валидацию OpenSpec и `git diff --check`; проверить по diff отсутствие изменений агента и новых зависимостей. При известном зависании Alembic на free-threaded Python повторить проверки в отдельном окружении обычного Python 3.14 и зафиксировать фактические результаты.

## Verification notes

- `PYTHONPATH=src:tests /private/tmp/lovogit-attention-verify/bin/python -m unittest discover -s tests`: 90 tests passed on standard CPython 3.14.0 with the project's existing locked dependencies. Used the separate verification environment because the working free-threaded interpreter has the previously documented Alembic timeout limitation.
- `ruff check src tests`, `openspec validate add-bot-repository --strict`, and `git diff --check` passed.
- GitHub HTTP and Telegram delivery were substituted in tests; no live repository registration or production migration was performed.
- Agent code, source-provider behavior, project dependencies, and unrelated output artifacts were not changed. Existing uncommitted attention work was preserved.
