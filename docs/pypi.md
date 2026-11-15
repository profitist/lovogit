# PyPI provider

`PyPiProvider` читает открытые HTTP API PyPI через переданный `httpx.AsyncClient`.
Не требует токенов бота, GitHub или LLM. Клиент создаёт и закрывает вызывающий код.

## Контракт

- `await get_package(name)` → `PackageIndexDTO`: версии и файлы из
  `GET /simple/{normalized-name}/` с
  `Accept: application/vnd.pypi.simple.v1+json`.
- `await get_package_info(name, version=None)` → `PackageInfoDTO`: метаданные
  из `GET /pypi/{normalized-name}/json` либо
  `GET /pypi/{normalized-name}/{version}/json`.

Имена нормализуются: регистр игнорируется, последовательности `.`, `_`, `-`
заменяются на `-`. Версия передаётся отдельным URL-компонентом; пустые значения,
пути, query и fragment не принимаются.

```python
import httpx

from lovogit.app.providers.pypi import PyPiProvider
from lovogit.cfg.pypi import PyPiSettings

async def inspect_package():
    async with httpx.AsyncClient() as client:
        provider = PyPiProvider(client, PyPiSettings())
        index = await provider.get_package("requests")
        release = await provider.get_package_info("requests", "2.32.3")
        return index, release
```

Индекс возвращает все объявленные версии в порядке сервера: они не сортируются
и не фильтруются. Включены prerelease и yanked-файлы. У каждого файла сохраняются
имя, URL, хеши, размер, ограничение Python, признак/причина yanked и время загрузки,
если оно опубликовано. Относительные URL разрешаются относительно URL индекса.
Ссылки не посещаются, файлы пакета не скачиваются и не исполняются.

Метаданные содержат описание и его content type, summary, `requires_python`,
`requires_dist`, `dynamic`, опубликованные ссылки и сведения о yanked-релизе.
`requires_dist` сохраняется без разбора markers/extras; значения могут быть
динамическими и не заменяют разрешение зависимостей для конкретного окружения.
Без `version` возвращается версия, выбранная самим endpoint PyPI, а не вычисленное
провайдером последнее совместимое обновление. Выбор версии с учётом Python,
ограничений проекта, prerelease и yanked должен выполнять сервис мониторинга.

`description` — описание пакета, обычно README, не гарантированные release notes.
`project_urls` сохраняет исходные названия ссылок: Source, Changelog,
Documentation и другие. Поиск и чтение changelog — отдельная задача.
Устаревшее поле JSON API `releases` не используется.

## Настройки

Читаются из окружения или `.env`, посторонние значения игнорируются:

| Параметр | По умолчанию |
|---|---|
| `API_URL` | `https://pypi.org` |
| `PYPI_CONNECT_TIMEOUT` | 10 секунд |
| `PYPI_READ_TIMEOUT` | 30 секунд |
| `PYPI_DEADLINE` | 60 секунд на запрос целиком |
| `PYPI_MAX_RESPONSE_BYTES` | 64 MiB распакованного тела ответа |

`API_URL` принимает HTTPS origin без пути, credentials, query и fragment;
например, `https://test.pypi.org`. Зеркалу нужны оба API и JSON Simple API 1.1+
в пределах major 1. Прежнее имя `PYPY_READ_TIMEOUT` принимается как alias;
при наличии обоих используется `PYPI_READ_TIMEOUT`. `PYPI_TOKEN` больше не нужен.

## Ошибки и сетевой контракт

В `pypi_errors.py` определены общий `PyPiError` и отдельные ошибки:

- `PackageNotFound`: HTTP 404 для проекта или запрошенного релиза;
- `PyPiRateLimited`: HTTP 429; `retry_after` хранит исходный Retry-After,
  который может быть числом секунд или HTTP date;
- `PyPiUnavailable`: HTTP 5xx, transport error или deadline;
- `InvalidPyPiResponse`: неожиданный статус/тип содержимого, повреждённый JSON,
  нарушение схемы, неподдерживаемая версия Index API, несовпадение имени проекта
  или превышение размера ответа.

Ошибки содержат имя пакета, запрошенную версию и доступный HTTP status,
но не тело ответа. Неверные входные аргументы вызывают `ValueError` до сети.
Отмена coroutine передаётся вызывающему без преобразования в ошибку провайдера.
Каждый ответ закрывается, переданный клиент остаётся открытым.

Запросы не наследуют cookies, Authorization и auth переданного клиента.
Редиректы не выполняются, автоматических повторов нет. Повторы при 429/5xx
и расписание мониторинга относятся к вызывающему сервису.

## Источники и проверки

- [PyPI JSON API](https://docs.pypi.org/api/json/)
- [PyPI Index API](https://docs.pypi.org/api/index-api/)
- [Simple Repository API specification](https://packaging.python.org/en/latest/specifications/simple-repository-api/)

Контрактные тесты используют `httpx.MockTransport`, не обращаются к сети и не
требуют токенов:

```sh
PYTHONPATH=src:tests .venv/bin/python -m unittest test_pypi_provider
```

## Анализ загруженных материалов

`UpdateSourceAnalysisService(extractor, settings).analyze(fetched)` принимает
готовый `UpdateSourceFetchResultDTO` и сохраняет его в результате. Статусы:
`no_materials`, `failed`, `partial`, `completed`. Документы имеют стабильные ID
по позиции входного результата; ошибки загрузки и происхождение сохраняются.
При отсутствии непустого fetched-текста модель не вызывается. Версии и
идентичность пакета проверяются до анализа; интервал — `(current, target]`.

Независимые настройки `SourceAnalysisSettings` читают переменные окружения:
`SOURCE_ANALYSIS_MAX_DOCUMENTS` (10), `SOURCE_ANALYSIS_MAX_DOCUMENT_CHARS` (60000),
`SOURCE_ANALYSIS_MAX_TOTAL_CHARS` (180000), `SOURCE_ANALYSIS_MAX_RESPONSE_CHARS`
(60000), `SOURCE_ANALYSIS_CALL_TIMEOUT` (30 секунд), `SOURCE_ANALYSIS_DEADLINE`
(120 секунд). Все значения положительные. Импорт не требует BOT_TOKEN или LLM
credentials; модель передаётся явно.

`UpdateChangeExtractor` принимает готовую LangChain chat model. Создающий её код
выбирает provider/model/credentials и задаёт конечный output-token limit;
локальная проверка длины ответа не ограничивает расход токенов провайдера.
Клиент модели принадлежит вызывающему и анализатором не закрывается.

Например, для текста `1.1 remove old` валидная запись модели выглядит так:

```json
{"category":"api","description":"remove old","symbol":"old","release_version":"1.1","evidence":[{"source_id":"source-0","quote":"remove old"}],"version_evidence":{"source_id":"source-0","quote":"1.1"}}
```

Сервис проверяет точное вхождение цитат и сохраняет их позиции и исходные links.
Указание `release_version: null` и `version_evidence: null` сохраняет такое
утверждение в `unscoped_changes` с `unknown_version`. Неподтверждённый символ,
неизвестный источник и отсутствующая цитата дают rejected; известная версия вне
интервала даёт outside_interval. Противоречащие записи не объединяются.
Цитата подтверждает опору на материал, но не правильность интерпретации.
Покрытие интервала всегда `undetermined`: даже completed означает обработку
переданных документов, а не полноту истории или совместимость.

Адаптер ожидает JSON `{ "changes": [...], "limitations": [...] }`, максимум
100 записей и 20 строк ограничений. Неизвестные поля отвергаются. Ошибки
`invalid_response`, `refused`, `model_error`, `response_limit`, `timeout`
отражаются на уровне документа и не скрывают результаты других документов.
Невалидная отдельная запись попадает в `rejected` с индексом и причиной.

```python
from lovogit.agent.update_sources.extractor import UpdateChangeExtractor
from lovogit.app.services.update_source_analysis import UpdateSourceAnalysisService
from lovogit.cfg.source_analysis import SourceAnalysisSettings


async def analyze_update_materials(fetched, model):
    settings = SourceAnalysisSettings()
    extractor = UpdateChangeExtractor(model, settings)
    return await UpdateSourceAnalysisService(extractor, settings).analyze(fetched)
```

Полный вызов с готовыми provider, loader и моделью:

```python
from lovogit.agent.update_sources.extractor import UpdateChangeExtractor
from lovogit.app.services.update_source_analysis import UpdateSourceAnalysisService
from lovogit.app.services.update_source_dto import UpdateSourceRequest
from lovogit.app.services.update_source_fetching import UpdateSourceFetchService
from lovogit.app.services.update_sources import UpdateSourceService
from lovogit.cfg.source_analysis import SourceAnalysisSettings


async def collect_fetch_analyze(provider, loader, model):
    sources = await UpdateSourceService(provider).collect(
        UpdateSourceRequest("example-pkg", "1.0", "2.0")
    )
    fetched = await UpdateSourceFetchService(loader).fetch(sources)
    settings = SourceAnalysisSettings()
    return await UpdateSourceAnalysisService(
        UpdateChangeExtractor(model, settings), settings
    ).analyze(fetched)
```

Fetch закрывает loader, анализ не закрывает модель. Документы передаются целиком:
превышение лимитов даёт пропуск, а не усечённый материал. Повторы и поиск других
источников не выполняются. Качество извлечения реальной моделью требует отдельной
оценки; проверки с fake model проверяют контракт и обработку результатов.

## Сбор материалов двух версий

Загрузка опубликованных ссылок — отдельный этап `UpdateSourceFetchService.fetch`:
сам `collect` не обращается к страницам. Результат сохраняет исходный `sources`,
признак `has_candidates` и записи `results`. Каждая запись содержит исходные
`links` с ролями и label, `status` (`fetched`, `skipped`, `failed`), `reason`,
исходный и конечный URL, цепочку редиректов, доступные HTTP status и Retry-After,
content type, кодировку и текст. Текст доступен только при `fetched`.

Например, для Documentation и относительной ссылки Changelog результат содержит
две записи `skipped` с причинами `not_change_link` и `invalid_url`, а
`has_candidates` равен false. При совпадении адресов двух релизов без fragment
обе исходные ссылки остаются в одной записи результата.

Настройки загрузки независимы от настроек бота и LLM и задаются через переменные
окружения: `SOURCE_FETCH_MAX_SOURCES` (10), `SOURCE_FETCH_MAX_REDIRECTS` (5,
допускается 0), `SOURCE_FETCH_CONNECT_TIMEOUT` (5 с),
`SOURCE_FETCH_READ_TIMEOUT` (10 с), `SOURCE_FETCH_SOURCE_DEADLINE` (20 с),
`SOURCE_FETCH_DEADLINE` (60 с), `SOURCE_FETCH_MAX_RESPONSE_BYTES` (2097152 байт).
Все остальные значения должны быть положительными.

Загрузчик принимает только HTTP и HTTPS на стандартных портах, без credentials.
Localhost, внутренние, служебные и смешанные публичные/внутренние DNS-ответы
отклоняются. Соединение устанавливается с проверенным IP, исходное имя остаётся
в Host и TLS. Каждый редирект проверяется отдельно; переход с HTTPS на HTTP
запрещён. Запросы анонимны: cookies и авторизация не отправляются, proxy из
окружения не используется. Эти ограничения также действуют для ссылок из PyPI.

Принимается только HTTP 200 с `text/plain`, `text/markdown` или `text/html`.
Тело декодируется строго по charset из Content-Type либо как UTF-8; сжатые
ответы отклоняются. HTML сохраняется как недоверенный исходный текст, без
выполнения скриптов и загрузки вложенных ресурсов. Содержимое может относиться
к нескольким релизам или быть навигацией страницы.

Причины ошибок включают `blocked_destination`, `invalid_url`,
`invalid_redirect`, `redirect_limit`, `http_error`, `transport_error`,
`timeout`, `size_limit`, `unsupported_content_type`,
`unsupported_content_encoding`, `unsupported_encoding`, `decode_error` и
`empty_content`. Пропуски по лимитам обозначаются `source_limit` и
`deadline_exceeded`. Ошибка или отсутствие ссылки не означает отсутствие
изменений и не является оценкой совместимости.

Пример объединяет оба независимых этапа. `fetch` закрывает загрузчик после
вызова; для следующего вызова создайте новый экземпляр.

```python
from lovogit.app.providers.source_document import SourceDocumentLoader
from lovogit.app.services.update_source_dto import UpdateSourceRequest
from lovogit.app.services.update_source_fetching import UpdateSourceFetchService
from lovogit.app.services.update_sources import UpdateSourceService
from lovogit.cfg.source_fetch import SourceFetchSettings


async def fetch_update_materials(provider, loader=None):
    sources = await UpdateSourceService(provider).collect(
        UpdateSourceRequest("example-pkg", "1.0", "2.0")
    )
    settings = SourceFetchSettings()
    loader = loader or SourceDocumentLoader(settings)
    return await UpdateSourceFetchService(loader, settings).fetch(sources)
```

Результат содержит опубликованный материал и его происхождение; он не является
доказательством совместимости и не подтверждает, что текст относится к конкретной
версии.

`UpdateSourceService(provider).collect(UpdateSourceRequest(...))` получает
метаданные конкретных current/target релизов через переданный PyPiProvider.
Запрос требует непустые package/current_version/target_version и не выбирает
последнюю версию. Одинаковая запрошенная версия читается один раз, но в результате
всегда присутствуют обе роли. Возвращённая PyPI версия и запрошенная строка
сохраняются отдельно: например, `1` и `1.0`.

```python
from lovogit.app.services.update_source_dto import UpdateSourceRequest
from lovogit.app.services.update_sources import UpdateSourceService

async def collect_update_materials(provider):
    request = UpdateSourceRequest("requests", "2.31.0", "2.32.3")
    return await UpdateSourceService(provider).collect(request)
```

`provider` — существующий PyPiProvider; клиент создаётся и закрывается вызывающим
кодом, как в примере выше. Сам сервис не загружает настройки бота или LLM.
Результат содержит request, current и target. В каждой записи metadata хранит
исходный PackageInfoDTO, origin — source_kind=`pypi_metadata`, роль, имя,
возвращённую и запрошенную версии; description_available указывает наличие
непробельного описания. Текст и content type сохраняются без преобразования.
Описание не объявляется release notes, а совпадение README не означает
отсутствия изменений библиотеки.

Отсутствие материалов — допустимый результат. Ошибки получения любого релиза
передаются как исходные исключения провайдера с контекстом версии и rate limit;
частичный результат не возвращается, автоматических повторов нет. Cancellation
также передаётся вызывающему.

### Опубликованные ссылки

`current.links` и `target.links` сохраняют исходные label/URL, поле происхождения
(`project_urls` или `home_page`), origin релиза, category и exclusion_reason.
Между версиями записи не объединяются. В частности, одинаковая ссылка у двух
версий остаётся двумя записями с разными ролями.

Категория определяется только по названию ссылки: lowercase с удалением
обычных пробелов, дефисов и подчёркиваний:

| Названия после нормализации | Категория |
|---|---|
| changelog, changes, history | changelog |
| releasenotes | release_notes |
| source, sourcecode, repository, code | source |
| documentation, docs | documentation |
| homepage, home | homepage |
| остальные | other |

`home_page` становится отдельной homepage-ссылкой, если значение непустое.
Неизвестные названия не распознаются по адресу: например, `Community` с URL
GitHub Releases остаётся `other`. Словарь намеренно конечный; README для поиска
ссылок не разбирается.

`has_change_links` доступен для current, target и всего результата (OR двух
релизов). Он учитывает только changelog/release_notes с абсолютным HTTP(S) URL,
непустым хостом, допустимым port и без credentials. Пробелы, управляющие символы
и обратные слеши в URL исключаются. Неподходящая запись сохраняется с причиной
`empty_url`, `invalid_url`, `unsupported_scheme` или `credentials_not_allowed`.
Query и fragment подходящего адреса сохраняются.

Пригодность здесь означает только соответствие формату: страницы не посещаются,
их доступность, содержимое и официальность не проверяются. Источник указан
автором пакета в PyPI. Если есть только source/docs, сбор успешен, но
`has_change_links` равен false; это не утверждение об отсутствии изменений.

### Различия требований и отзыв релиза

`requirements_diff` содержит current_requires_python, target_requires_python,
requires_python_changed и отсортированные added_requires_dist/removed_requires_dist.
Ограничения Python сравниваются как исходные значения, включая None; различное
написание семантически одинаковых ограничений считается различием метаданных.
Требования зависимостей сравниваются как множества точных строк: перестановка
и повторы не дают изменений. Исходный порядок и повторы доступны в metadata.
Markers и extras не вычисляются, транзитивные зависимости не разрешаются.

Для каждой роли `requires_dist_dynamic` отмечает наличие `requires_dist` в
metadata.dynamic. Все значения dynamic сохраняются. PyPiProvider уже преобразует
null requires_dist в пустой tuple: сервис не восстанавливает это различие и
не считает пустой список доказательством отсутствия зависимостей окружения.

Отзыв целевого релиза доступен как `target.metadata.yanked` и
`target.metadata.yanked_reason`. Причина может быть пустой или None, не отменяя
сам факт отзыва. Сервис не выбирает другую версию и не присваивает статус
совместимости ни на основании отзыва, ни на основании различий требований.
