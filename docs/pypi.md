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
