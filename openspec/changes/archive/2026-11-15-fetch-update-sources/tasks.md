# Tasks

## 1. Контракт и планирование загрузок

- [x] 1.1 Добавить frozen DTO в `app/services/update_source_fetch_dto.py` и каркас `UpdateSourceFetchService` в `update_source_fetching.py` по design; проверить в `tests/test_update_source_fetching.py` сохранение исходного sources, отсутствие кандидатов и пропуски неподходящих категорий/exclusion_reason без обращения к загрузчику.
- [x] 1.2 Реализовать группировку URL без fragment и детерминированный обход; проверить один вызов для общих current/target ссылок, сохранение всех labels/origins/fragment, отдельные query и отсутствие объединения по конечному URL.
- [x] 1.3 Описать DTO, fetched/skipped/failed и независимость collect от fetch в `docs/pypi.md`; сверить примеры результата с тестовыми фикстурами.

## 2. Проверяемый сетевой транспорт

- [x] 2.1 Добавить `cfg/source_fetch.py` с независимыми настройками и значениями из design; проверить значения по умолчанию, допустимый нулевой лимит редиректов и отказ на недопустимые границы без настроек бота/LLM. Описать переменные окружения и лимиты в `docs/pypi.md`.
- [x] 2.2 Объявить прямую зависимость httpcore в `pyproject.toml` и обновить lock; реализовать проверяющий resolver/network backend и адаптер httpx в `app/providers/source_document_transport.py`. В `tests/test_source_document_transport.py` проверить fake backend: соединение идёт к проверенному числовому IP, Host/SNI сохраняются, повторного DNS lookup нет, proxy окружения и credentials не используются.
- [x] 2.3 Реализовать проверку начальных адресов и новых соединений; тестами транспорта проверить loopback/private/link-local/reserved/multicast/unspecified, IPv4-mapped IPv6, localhost, смешанные DNS-ответы, DNS failure, смену DNS, credentials, повреждённые URL и нестандартные порты; запрещённые назначения не должны достигать connect.
- [x] 2.4 Описать в `docs/pypi.md` ограничения адресов, портов, DNS и изоляцию запросов; сверить с тестами, включая отсутствие отправки cookies и отключение proxy из окружения.

## 3. Загрузка документа

- [x] 3.1 Добавить загрузчик `app/providers/source_document.py` с явными GET и ручными редиректами. В `tests/test_source_document.py` через MockTransport и fake backend проверить относительный Location, публичную смену хоста, внутреннее назначение, цикл, лимит, неверный/отсутствующий Location, запрет https→http, отсутствие auth/cookies на всех переходах и отсутствие retries.
- [x] 3.2 Реализовать потоковое чтение, ограничения Content-Length/фактического тела, connect/read timeout и deadline источника с DNS и редиректами; проверить медленный DNS, поток без Content-Length, превышение размера, транспортную ошибку, отмену и закрытие ответа во всех ветках без частичного успешного текста.
- [x] 3.3 Реализовать проверку статуса, media type, Content-Encoding и строгое декодирование по design; проверить plain/Markdown/HTML, charset и UTF-8 по умолчанию, unknown charset, decode_error, пустой текст, бинарный ответ, gzip, HTTP 404/429/500, сохранение status/Retry-After и отсутствие вложенных запросов или исполнения HTML.
- [x] 3.4 Дополнить `docs/pypi.md` поддержанными типами/кодировками, причинами ошибок и ограничением исходного HTML; сверить описание с тестами загрузчика.

## 4. Оркестрация и границы вызова

- [x] 4.1 Подключить загрузчик к сервису, лимит групп и общий deadline; проверить в `tests/test_update_source_fetching.py` частичный успех, все источники failed, отсутствие кандидатов, пропуски по source_limit/deadline_exceeded, сохранение готовых материалов и передачу внешней отмены с освобождением клиента.
- [x] 4.2 Добавить интеграционный тест collect → fetch с настоящими сервисами и подменёнными HTTP/DNS/backend: проверить точные версии PyPI, отсутствие загрузок при одном collect, provenance после редиректа и отсутствие обращений к БД/боту/LLM/comparer. Проверить API без обязательных настроек этих компонентов.
- [x] 4.3 Добавить исполнимый пример collect → fetch в `docs/pypi.md` с управлением временем жизни загрузчика; выполнить его на фикстуре без сети и проверить, что материал не объявляется доказательством совместимости или текстом конкретного релиза.

## 5. Итоговая интеграционная проверка

- [x] 5.1 Выполнить `PYTHONPATH=src:tests .venv/bin/python -m unittest test_source_document_transport test_source_document test_update_source_fetching test_update_sources test_pypi_provider`, затем `PYTHONPATH=src:tests .venv/bin/python -m unittest discover -s tests`; зафиксировать фактические результаты и пропуски, включая Docker-интеграционные тесты.
- [x] 5.2 Выполнить Ruff check и format --check затронутых Python-файлов, `git diff --check` и `openspec validate fetch-update-sources --strict`; проверить согласованность документации, DTO и всех сценариев спецификации, записать результаты в этот файл.

## Результаты проверки

- Профильный набор (включая `test_source_fetch_settings`): 55 тестов, OK.
- Полный `unittest discover`: 186 тестов, OK, 8 пропущены (7 Docker-интеграционных и один условный).
- Ruff check и format --check девяти затронутых Python-файлов: пройдены.
- `openspec validate fetch-update-sources --strict` и `git diff --check`: пройдены.
- После уточнения приоритета общего deadline повторно пройдены 6 тестов сервиса, Ruff для изменённого модуля и валидация OpenSpec.
