# Tasks

## 1. Контракт, обнаружение и ограниченное чтение

- [x] 1.1 Добавить `app/dependencies/dto.py`: inventory, декларации, constraints, provenance, lock candidates, diagnostics и limits; проверить тестами nullable current_version, состояния разбора/версии и сохранность значений после удаления исходных файлов.
- [x] 1.2 Реализовать выбор auto/pyproject/requirements с приоритетом корневого PEP 621 и перечнем невыбранных файлов; тестами проверить оба манифеста, tool-only pyproject, явный выбор, отсутствующий источник и запрет fallback после неверного TOML.
- [x] 1.3 Реализовать общий reader с относительными путями, SHA-256 и бюджетами из design; проверить fixtures на разрешённый `..` внутри корня, выход наружу, symlink, специальный файл, каждый лимит, недопустимые limits и повторное чтение без обхода бюджета.
- [x] 1.4 Создать `docs/dependencies.md` с контрактом статусов, источников и начальных лимитов; сверить примеры с тестами обнаружения и явно описать отсутствие записи в `dependencies`, вызовов сети и измерения установленного окружения.

## 2. Декларации и объявленные фиксации

- [x] 2.1 Реализовать разбор PEP 621 runtime и optional-dependencies через общий разбор требований; тестами проверить normalisation, extras, markers, provenance TOML, пустые/отсутствующие поля, dynamic, ошибочные типы/элементы, неподдержанные dependency-groups и игнорирование build-system.requires.
- [x] 2.2 Реализовать поддержанное подмножество requirements с BOM, комментариями, URL fragments, continuation, hashes и всеми оговорёнными формами локальных -r/-c; тестами проверить режимы requirement/constraint, provenance строк, повторные включения, цикл, missing include, unsupported options, env substitutions и remote includes без обращения к ним.
- [x] 2.3 Реализовать общую классификацию declared_pin/unresolved/conflict/unsupported и применение constraints; проверить тестами pins, `===`, wildcard, диапазоны, совместимые и конфликтующие ограничения, constraint-only пакеты, aliases имён, marker-варианты, conditional constraints и direct URL/VCS/editable без ложной текущей версии.
- [x] 2.4 Дополнить `docs/dependencies.md` таблицей поддержанного синтаксиса, примерами constraints и различием complete/partial и неизвестной версии; проверить документированные примеры на тех же локальных fixtures, включая отсутствие привязки markers к окружению контроллера.

## 3. Подтверждение версий по uv.lock

- [x] 3.1 Реализовать чтение layout version=1/revision=3 и идентификацию единственного корневого проекта; тестами проверить virtual/editable root, неверный TOML, неизвестную revision, неоднозначный root и workspace без запуска uv/pip и без выбора версии из неподдержанного lock.
- [x] 3.2 Реализовать сопоставление корневых metadata.requires-dist, optional областей и requires-python с манифестом; проверить reordered/нормализованные эквиваленты, изменение specifier с всё ещё подходящей старой версией, extras/markers, отсутствующие metadata, dynamic и неподдержанные overrides/sources с диагностикой несогласованности.
- [x] 3.3 Реализовать выбор прямых registry candidates и locked/unresolved/conflict с provenance; тестами проверить единственную версию, missing entry/edge, конфликт pin, несколько версий, resolution-markers, неоднозначные sources, сохранение кандидатов и исключение транзитивных пакетов/самого проекта из прямого инвентаря.
- [x] 3.4 Документировать ограничения uv.lock, stale metadata и отсутствие гарантии installed state; проверить примеры согласованного, неоднозначного и неподдержанного lock fixtures, в том числе запрет скрытого fallback к declared_pin при повреждённом lock.

## 4. Сборка и сквозная проверка

- [x] 4.1 Собрать публичную `read_dependency_inventory` и агрегацию complete/partial/failed/no_sources; интеграционными тестами проверить обе ветки источников, декларации с неизвестными версиями, частичную ошибку и стабильный порядок результата при повторном разборе одного снимка.
- [x] 4.2 Добавить проверяемый пример `RepoService.open_source` → `asyncio.to_thread(read_dependency_inventory, snapshot)` с mocked GitHub и локальным архивом; проверить SHA/provenance, доступность DTO после cleanup и неизменность файлов, RepoDependency и истории мониторинга.
- [x] 4.3 Проверить автономность импортов без bot/LLM credentials и отсутствие subprocess/сети/выполнения backend; выполнить локальный smoke-разбор собственного pyproject/uv.lock и сверить прямые имена с манифестом, а подтверждённые версии с lock без установки зависимостей.
- [x] 4.4 Запустить профильные тесты, полный `unittest discover`, Ruff, `git diff --check` и `openspec validate discover-project-dependencies --strict`; зафиксировать фактические результаты и проверить, что diff реализации не включает посторонние изменения comparer.
