# TestSandbox

`lovogit.sandbox.TestSandbox` — независимый асинхронный Python API. Он не
импортирует LLM или LangGraph, не анализирует release notes. Сравнение —
детерминированная оценка предоставленных проверок, не заключение о полной
совместимости проекта.

## Интеграция с проектом

`GitHubProvider.resolve()` получает SHA **default branch** (не обязательно
`master`); `snapshot()` скачивает ZIP именно по этому SHA и отдаёт временный
`SourceSnapshotDTO`. `RepoService.open_source()` ограничивает время жизни
исходников контекстным менеджером. Передавайте snapshot в `prepare_environment`
до выхода из этого контекста. Sandbox сохраняет собственный снимок, поэтому
дальнейшие запуски не зависят от жизни скачанного каталога. Происхождение SHA
доверяется провайдеру: произвольный локальный каталог сам по себе не доказывает
принадлежность указанному коммиту. Дополнительно проверяется fingerprint файлов.

В приложении уже используются uv, Pydantic и отдельные `BaseSettings` в `cfg`.
Docker SDK и общего artifact store не было. Используется Docker CLI через
`asyncio.create_subprocess_exec`, без shell и без Docker-in-Docker. Требование
управляющего приложения `Python >=3.14` не изменено; сам пакет использует синтаксис
Python 3.11+. Независимому пользователю пакета нужны `pydantic-settings` и Docker
CLI. Зависимости приложения и существующий `uv.lock` не менялись.

`ComparerState` находится в `agent/comparer/state.py` и наследует компактный
`SandboxState`. Старые эскизные модели окружений/результатов заменены ID и краткими
сводками. `SandboxNodes` в `agent/comparer/sandbox.py` предоставляет async-узлы,
возвращающие частичные обновления: `prepare_baseline`, `freeze_bundle`,
`run_baseline`, `prepare_candidate`, `run_candidate`, `compare`. Существующий
`graph.py` остаётся эскизом с неопределёнными узлами генерации/анализа: этот модуль
не превращает его в готовый LLM workflow. Сборка графа остаётся ответственностью
приложения. В State не попадают отчёты, исходники, списки пакетов и Docker-объекты.

## Состав

- `sandbox/api.py`: координация, workspace, bundle, lifecycle и сравнение.
- `sandbox/models.py`: общие Pydantic DTO.
- `sandbox/storage.py`: безопасные пути, снимки, SHA-256, атомарные JSON-записи,
  content-addressed артефакты и межпроцессный lock.
- `sandbox/docker.py`: контейнеры, volumes, лимиты, перенос данных, cleanup.
- `sandbox/reports.py`: разбор pytest JSON, классификация исходов, traceback.
- `docker/sandbox/worker.py`: доверенная точка входа контейнера для uv/pytest.
- `cfg/sandbox.py`: настройки через существующий механизм Pydantic Settings.

Хранилище по умолчанию — `output/sandbox`: `environments`, `snapshots`,
`workspaces`, `bundles`, `bundle_files`, `runs`, `resolved`, `keys`, `artifacts`.
Идентификаторы не используются как произвольные файловые пути. Большие результаты
читаются отдельно через `read_artifact`; диапазоны строк включительные, от 1.
Стандартные JSON-отчёты содержат также полные traceback всех ошибок; компактный
ответ содержит до 20 ошибок, 500 символов сообщения и 2000 символов traceback.

## Образ и настройки

```sh
docker build -t lovogit-sandbox:local docker/sandbox
```

По умолчанию образ использует Python 3.12.10, uv 0.9.7, pytest 8.3.5,
pytest-json-report 1.5.0, pytest-cov 6.0.0. Для другого Python соберите образ
с `--build-arg PYTHON_VERSION=3.13.3` и задайте его через `SANDBOX_IMAGE`.
Образ должен быть доверенным; runtime сохраняет и использует его immutable image ID,
а не разрешает tag заново при каждом прогоне. Для production закрепляйте также
base image по digest и храните собранные образы в своём registry. Patch-версия
Python из образа фиксируется в метаданных, даже если запрошено только `3.12`.

```python
from pathlib import Path
from lovogit.cfg.sandbox import SandboxSettings, SandboxLimits

settings = SandboxSettings(
    root=Path("output/sandbox"),
    image="lovogit-sandbox:local",
    preparation_network="none",
    limits=SandboxLimits(cpus=1, memory_mb=1024, pids_limit=128,
                         timeout_seconds=120, max_output_bytes=1_000_000),
)
```

Поддерживаются переменные `SANDBOX_ROOT`, `SANDBOX_IMAGE`,
`SANDBOX_PREPARATION_NETWORK`, `SANDBOX_INTEGRATION_NETWORK`,
`SANDBOX_PREPARATION_TIMEOUT_SECONDS` (600), `SANDBOX_MAX_SNAPSHOT_BYTES`
(128 MB), `SANDBOX_MAX_ARTIFACT_BYTES` (10 MB),
`SANDBOX_LIMITS__MEMORY_MB` и аналогичные вложенные настройки.
`.env` читается только управляющим процессом и не передаётся контейнерам.

**Сеть:** default `none` при обеих фазах. Для offline подготовки пакеты должны
быть доступны в `/opt/wheels` образа: базовый образ уже содержит wheels harness.
Для реальных проектов дополните wheelhouse либо задайте отдельную custom Docker
network, для которой оператор настроил egress allowlist/proxy. Само имя Docker
network не фильтрует домены; сервис не создаёт firewall и не утверждает, что
произвольная custom network безопасна. `host`, `bridge`, `default` и режимы
`container:...` запрещены. Индексы/URL из проекта также ограничиваются внешней
сетевой политикой. Секреты, PyPI tokens, proxy credentials управляющего процесса
не пробрасываются. Интеграционные тесты с сетью требуют и настройки
`integration_network`, и `run_tests(..., allow_network=True)`.

## API

```python
from lovogit.sandbox import TestSandbox

sandbox = TestSandbox(settings)
await sandbox.recover()  # один раз при старте процесса

baseline = await sandbox.prepare_environment(
    repository_id="project-a",
    repository_sha="0123456789abcdef0123456789abcdef01234567",
    repository_path="/tmp/repos/project-a",
    python_version="3.12",
)
if baseline.status != "ready":
    raise RuntimeError(baseline.reason)

artifact = await sandbox.write_test(
    baseline.workspace_id, "test_client.py",
    "from api.client import request\n"
    "def test_request():\n    assert request() is not None\n",
)
bundle = await sandbox.freeze_test_bundle(
    baseline.workspace_id,
    [artifact.path, "repository/tests/test_client.py::test_timeout"],
)
base_run = await sandbox.run_tests(
    baseline.environment_id, bundle.bundle_id, idempotency_key="analysis-42:baseline",
)
# Агент проверяет baseline; при изменении тестов нужен новый bundle и baseline.
if base_run.status == "passed" and base_run.passed > 0:
    candidate = await sandbox.prepare_environment(
        "project-a", baseline.repository_sha, None, "3.12",
        {"httpx": "0.28.1"}, baseline_environment_id=baseline.environment_id,
    )
    candidate_run = await sandbox.run_tests(
        candidate.environment_id, bundle.bundle_id,
        idempotency_key="analysis-42:candidate",
    )
    comparison = await sandbox.compare_runs(base_run.run_id, candidate_run.run_id)
    if comparison.introduced_failures:
        confirmation = await sandbox.run_tests(candidate.environment_id, bundle.bundle_id)
        comparison = await sandbox.compare_runs(
            base_run.run_id, candidate_run.run_id,
            confirmation_run_id=confirmation.run_id,
        )
    await sandbox.cleanup_environment(candidate.environment_id)

coverage = await sandbox.get_coverage(base_run.run_id, "api/client.py")
# Агент проверяет пересечение coverage.executed_lines с затронутыми строками.
result = await sandbox.get_test_results(base_run.run_id)
if result.report_artifact_id:
    report = await sandbox.read_artifact(result.report_artifact_id, 1, 30)
await sandbox.cleanup_environment(baseline.environment_id)
# result, отчёты и immutable bundle сохраняются после cleanup.
```

Селекторы — конкретные `.py` файлы с префиксом `generated/` или `repository/`,
включая pytest node IDs. Для набора существующих тестов перечислите файлы.
Произвольные pytest flags и shell-команды не принимаются. Прочие generated helper
модули и `conftest.py` включайте явно в bundle: snapshot содержит только выбранные
файлы. Для node IDs hash вычисляется по всему файлу. После изменения теста старый
bundle не меняется, новый получает другой fingerprint. SHA, хеш исходников и bundle
должны совпадать между окружениями.

## Установка и воспроизводимость

Baseline с `uv.lock` проверяется `uv lock --locked` и устанавливается
`uv sync --locked --no-editable`. Без lock uv создаёт его; это разрешение
требований на момент подготовки, а не реконструкция неизвестного окружения
разработчика. `requirements.txt` обрабатывается `uv pip compile` / `uv pip sync`.
Текущий зафиксированный requirements не переписывается для снятия ограничений.
Отдельный `poetry.lock` без uv.lock возвращает `blocked`: сначала экспортируйте
locked requirements или мигрируйте lock в uv вне sandbox.

Candidate с `baseline_environment_id` использует конкретный baseline; при
`repository_path=None` берёт сохранённый snapshot, что позволяет продолжить после
удаления временного checkout. Без ссылки автоматически находит/готовит baseline, получает его разрешённый lock
и выполняет targeted `--upgrade-package name==version`. uv предпочитает сохранить
остальные версии, но может изменить транзитивные зависимости. Проверяются реально
установленная версия и `uv pip check`, сохраняются все package versions,
`dependency_changes`, исходный fingerprint lock/config и разрешённый lock artifact.
Ограничения проекта не ослабляются. Harness устанавливается с constraints уже
установленных версий; конфликт harness с проектом означает `blocked`.
Подготовка не должна менять существующие файлы исходников, кроме lock/resolution.
Проекты с нативными сборками требуют подходящих системных библиотек в образе.

## Lifecycle и результаты

`preparing → ready | blocked → deleted`. `get_environment` читает состояние.
Создание идемпотентно по SHA, hash исходников, Python, образу, overrides и сети.
Дополнительный `idempotency_key` нельзя переиспользовать с другими входами.
Запуски без ключа всегда новые, с ключом возвращают сохранённый результат.
`recover()` под общим lock завершает оставшиеся `running/preparing` операции как
`interrupted/blocked`, удаляет их контейнеры/volumes. Повторить interrupted run
можно новым ключом; blocked environment — после cleanup или с новым ключом.
Lock намеренно сериализует операции одного store, включая разные процессы.
Хранилище должно быть на локальном файловом разделе с поддержкой `flock`.

Статусы исполнения: `passed`, `failed`, `collection_error`, `no_tests`, `timeout`,
`infrastructure_error`, `preparation_error`, `resource_limit`, `interrupted`.
`running` означает незавершённую операцию. OOM определяется Docker State,
а CPU задаётся квотой; отдельного достоверного «CPU exhausted» события Docker нет,
длительная работа завершается timeout. Missing/invalid report не превращается
в доказательство регрессии. Логи ограничены размером и не считаются бесконечными.

`compare_runs` проверяет эквивалентность исходников, bundle, Python, платформы,
образа, переменных и сети; сравнивает node IDs, outcomes, setup/call/teardown,
ошибки, dependency diff и покрытие. `breaking` требует независимого повторения
ошибок candidate. Пропуски/исчезнувшие проверки, снижение покрытия, недоказанная
воспроизводимость дают `inconclusive`; исходные ошибки — `baseline_failed`;
инфраструктурные — `blocked`. `compatible` относится только к данным проверкам.
Coverage возвращает строки и ветки; line coverage не доказывает вызов конкретного
внешнего API. Точечный runtime tracing оставлен отдельным будущим расширением.

## Docker isolation

- UID/GID `1000:1000`; без privileged, `cap-drop=ALL`, no-new-privileges.
- Стандартный seccomp Docker; он не отключается и должен обновляться вместе с runtime.
- CPU quota, memory и swap limit, PID limit, nofile limit, wall-clock timeout.
- Read-only root; `/tmp` — ограниченный tmpfs; `/artifacts` — отдельный writable volume.
- `/workspace` writable только при подготовке, read-only при тестировании.
- Generated bundle — единственный host bind mount, только read-only.
- Исходный repository, Docker socket, домашний каталог, env/secrets host не монтируются.
- Сеть `none` по умолчанию; приватный IPC, без host network, Docker logs отключены.
- Source symlinks/special files запрещены; `.git`, `.venv`, `.env*`, `.ssh`, `.aws`
  не переносятся. Docker cp отчётов принимает только bounded regular-file tar entries,
  никогда не распаковывает контейнерные архивы в произвольный host path.

Контейнеризация не является абсолютной защитой от недоверенного кода. Для более
сильной границы используйте выделенные workers/VM или gVisor/Kata. Отчёты pytest
создаются рядом с исполняемым кодом и являются данными, а не криптографическим
доказательством: целенаправленно вредоносный проект может подделывать свои результаты.

## Проверки

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_sandbox*.py' -v

# Отдельная Docker suite: весь исполняемый fixture-код запускается в контейнерах.
docker build -t lovogit-sandbox:local docker/sandbox
docker build -f docker/sandbox/Dockerfile.test -t lovogit-sandbox:test docker/sandbox
SANDBOX_DOCKER_TESTS=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_sandbox_docker.py -v
```

При pytest интеграционная suite отмечена `docker`; marker регистрируется в
`.github/workflows/sandbox.yml`. CI собирает offline wheelhouse для fixture,
затем проверяет baseline/candidate, exact version, pytest failures/collection/no
tests, CPU/RAM, timeout, изоляцию, отсутствие сети, coverage, restart и retention.
Подготовка и тестирование в CI работают с `network=none`.

## Ограничения и развитие

- Реальные Docker-проверки обязательны перед эксплуатацией: локально daemon может
  отсутствовать. Unit/contract mocks не доказывают работу kernel isolation.
- Disk quota для Docker volumes и автоматический retention/GC артефактов пока
  настраиваются оператором; размер считывания/ответа ограничен, но это не disk quota.
  Root store, Docker storage и snapshots требуют внешней квоты/мониторинга.
- Нет встроенного egress proxy/allowlist, доступа к private indexes с credentials,
  Windows controller (используется POSIX flock), распределённого scheduler и
  автоматического GC временных снимков, оставшихся до записи метаданных.
- Pytest plugin autoload и project `addopts` отключены для фиксированного harness.
  Проектам с pytest-asyncio и другими обязательными plugins нужен будущий строгий
  allowlist plugins; нельзя передавать произвольные CLI args от AI.
- Tests должны работать с read-only checkout. Fixtures, которым нужны записи,
  следует направлять в pytest tmp_path. Нативные extension builds и нестандартные
  import layouts могут потребовать отдельного доверенного image.
- Нет отдельного сервиса метрик; duration сохраняется для каждого run. Дальше:
  метрики подготовки/cache hit, квоты storage, retention, targeted tracing,
  явные baseline/candidate provenance links и более параллельное планирование.

## Проверка в рабочем окружении

2026-11-08: общий unittest discovery — 131 тест, OK, 8 skipped; из них sandbox —
26 unit/contract passed, 7 Docker integration skipped. Ruff check и format check
пройдены для всех затронутых Python-модулей. Импорт ComparerState/SandboxNodes
проверен. Docker CLI доступен, daemon socket отсутствует, поэтому реальные
контейнерные сценарии локально не подтверждены.
