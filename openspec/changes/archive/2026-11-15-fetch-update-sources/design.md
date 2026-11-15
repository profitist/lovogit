# Design

## Context

См. proposal.md для мотивации. `UpdateSourceService.collect` возвращает frozen dataclass `UpdateSourcesDTO`; ссылки содержат `SourceOriginDTO`, label, category и exclusion_reason. Его проверка URL синтаксическая, не разрешение на сетевой доступ. Существующая спецификация запрещает этому сервису читать опубликованные URL, поэтому новый этап вызывается отдельно.

`PyPiProvider` уже использует потоковый httpx, явные таймауты и закрытие ответа. Но его клиент предназначен для фиксированного API, поэтому повторное использование для произвольных URL неприемлемо. Тесты используют unittest/IsolatedAsyncioTestCase и MockTransport. Настройки PyPI независимы от бота и LLM; новый этап сохраняет эту возможность.

## Goals / Non-Goals

**Goals:** отдельный вызов загрузки с ограниченной сетью, проверяемым происхождением и результатами, пригодными для следующего этапа анализа.

**Non-Goals:** преобразование HTML в основной текст, выделение версий из changelog, GitHub API, переписывание blob URL в raw URL, headless browser, выполнение содержимого, кеш между вызовами и интеграция в граф.

## Decisions

### 1. Отдельные сервис и DTO

Добавить `UpdateSourceFetchService.fetch(sources: UpdateSourcesDTO)` в `app/services/update_source_fetching.py`, DTO в `update_source_fetch_dto.py`. Сервис получает загрузчик через конструктор. Результат содержит исходный `sources`, `has_candidates` и tuple записей с `links: tuple[SourceLinkDTO, ...]`, `status: fetched|skipped|failed`, reason, requested_url, final_url, redirect_chain, status_code, retry_after, content_type, encoding, text. Поля недоступного HTTP-контекста равны None; text есть только у fetched.

Альтернатива — расширить collect — нарушает существующий контракт. Наследование DTO не требуется: исходный результат включается целиком, поэтому descriptions и metadata не теряются и не выдаются за release notes.

### 2. Детерминированная обработка

Обход current.links, затем target.links, в исходном порядке. Неподходящая категория → skipped/not_change_link; exclusion_reason → skipped с исходной причиной. Кандидаты группируются по сериализованному httpx URL без fragment (query сохраняется); каждая группа хранит все исходные ссылки. Только одинаковый начальный адрес объединяется; совпавшие после редиректа страницы не объединяются. Один failed результат группы относится ко всем её links.

Загрузка последовательная: это упрощает общий deadline, лимиты и отмену. Автоматический поиск по source/docs и fallback к README отвергнуты, поскольку меняют смысл источника.

### 3. HTTP и защита назначения

Добавить `app/providers/source_document.py` и узкий транспорт `source_document_transport.py`. Загрузчик владеет выделенным async-клиентом с `trust_env=False`, без auth и cookies; каждый запрос создаётся явно как в PyPiProvider, cookies из ответов не переносятся. GET с User-Agent LovoGit, Accept для трёх поддержанных типов, Accept-Encoding identity; остальные Content-Encoding отклоняются как unsupported_content_encoding, что исключает распаковку неограниченного тела. TLS verification включена.

Перед каждым переходом повторить синтаксическую проверку URL независимо от exclusion_reason. Допускаются http:80 и https:443, отклоняются credentials, localhost (включая поддомены), управляющие символы, обратные слеши, пустой host и нестандартные порты. IP literal должен быть глобальным unicast; DNS разрешается асинхронно, все адреса должны пройти ту же проверку. IPv4-mapped IPv6 проверяется также как IPv4; multicast, unspecified, reserved, loopback, private и link-local запрещены.

Транспорт использует проверяющий async network backend для httpcore: разрешает hostname, проверяет полный набор адресов и соединяется с выбранным числовым IP без нового DNS lookup. Исходные hostname/Host и TLS SNI сохраняются для проверки сертификата. Проверка выполняется на каждом новом соединении; connection pool ограничен временем одного fetch-вызова. `httpcore` объявить прямой зависимостью, так как используется его API; не обращаться к приватным атрибутам httpx. Resolver и сетевой backend подменяются тестами. Простая предварительная DNS-проверка с последующим обычным connect отвергнута из-за разрыва между проверкой и использованием.

Редиректы только 301/302/303/307/308, Location разрешается относительно текущего URL, fragment снимается для запроса. Каждый переход проверяется; https→http запрещён. Отсутствующий/неверный Location → invalid_redirect; цикл и превышение числа переходов → redirect_limit. Прочие не-200 → http_error с кодом и Retry-After. Никаких retries, HEAD, загрузки вложенных ресурсов или обработки JS.

### 4. Ресурсные ограничения

Добавить независимый `SourceFetchSettings` в `cfg/source_fetch.py`: максимум 10 уникальных кандидатов, 5 редиректов на источник, connect timeout 5 с, read timeout 10 с, deadline источника 20 с (DNS + все переходы + тело), общий deadline 60 с и 2 MiB тела каждого ответа. Положительные численные настройки, redirects допускает 0. На вызов максимум 60 GET и 20 MiB успешных тел при значениях по умолчанию; без фоновых задач и параллельных загрузок.

Лимит тела проверяется по Content-Length при наличии и при потоковом чтении, включая ответы редиректов (их тела не читаются). Превышение → size_limit; частичный текст не возвращается. После 10 групп следующие → skipped/source_limit. Deadline источника → failed/timeout; общий deadline → failed/timeout текущему и skipped/deadline_exceeded оставшимся. Внешний CancelledError не перехватывается как штатная ошибка. finally закрывает ответы и клиент, включая отмену. Ошибки программирования не маскируются общим catch Exception.

### 5. Текст и причины результата

Успех только для 200 и media type text/plain, text/markdown, text/html без учёта регистра и параметров. charset берётся из Content-Type, иначе UTF-8; декодирование строгое, без угадывания и replacement. Неизвестный charset → unsupported_encoding; неверные байты → decode_error; пустой/пробельный текст → empty_content. Сохраняется исходный декодированный текст, включая HTML, без нормализации пробелов. Он считается недоверенным материалом, а не подтверждённым changelog конкретной версии.

Другие причины failed: blocked_destination, invalid_url, invalid_redirect, redirect_limit, http_error, transport_error, timeout, size_limit, unsupported_content_type, unsupported_content_encoding. DNS failure → transport_error; запрещённый адрес → blocked_destination. Нет общего флага «изменений нет»: наличие кандидатов и количество fetched вычисляются отдельно.

## Risks / Trade-offs

- [HTML содержит навигацию или не загружает текст без JS] → сохранять как исходный материал; очистку и проверку релевантности вынести в следующий change.
- [Публичный хост использует смешанные DNS-адреса или нестандартный порт] → предсказуемый отказ с причиной; не ослаблять проверку автоматически.
- [Транспорт сложнее обычного HTTP-клиента] → изолированный модуль и проверки фактического IP, SNI, proxy и cookies на уровне fake backend, а не только MockTransport.
- [Небольшие лимиты пропускают длинный changelog] → явный size_limit и настраиваемые границы, без выдачи обрезанного материала за полный.

## Migration Plan

Изменение аддитивное, миграций БД нет. Добавить настройки, сервис, тесты и пример collect → fetch в docs/pypi.md. Существующие вызовы collect работают без изменений. Откат — прекратить вызов нового сервиса; сохранённого состояния нет.
