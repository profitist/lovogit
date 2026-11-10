from collections.abc import Callable, Iterable
from functools import wraps
from pathlib import Path
from typing import Concatenate

from code_symbol_index import (
    CallGraph,
    Inspection,
    InspectOptions,
    Page,
    Repository,
    Symbol,
)

from lovogit.index.builder import RepoIndex, get_repo_index


def need_index[**P, R](
    func: Callable[Concatenate[Toolkit, P], R],
) -> Callable[Concatenate[Toolkit, P], R]:
    """Проверить наличие индекса перед вызовом метода, сохранив его метаданные.

    Raises:
        RuntimeError: В RepoIndex отсутствует индекс репозитория.
    """

    @wraps(func)
    def wrapper(self: Toolkit, *args: P.args, **kwargs: P.kwargs) -> R:
        if self.repository is None:
            raise RuntimeError(f"Cannot call {func.__name__}: index not initialized")
        return func(self, *args, **kwargs)

    return wrapper


class Toolkit:
    """Инструменты чтения индекса кода без изменения его содержимого.

    Принимает RepoIndex с индексом символов и деревом файлов репозитория.
    Ошибки поиска и разрешения символов передаются из code_symbol_index.
    """

    def __init__(self, repo_index: RepoIndex) -> None:
        """Принять индекс репозитория вместе с деревом файлов.

        Args:
            repo_index: RepoIndex с подготовленным индексом символов. Конструктор
                не запускает построение или обновление индекса.
        """
        self._repo_index = repo_index

    @need_index
    def search_symbols(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        limit: int = 20,
    ) -> list[Symbol]:
        """Найти символы по одному запросу.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            limit: Максимальное число результатов; в графе — ограничение ветвления.

        Returns:
            Список найденных символов.
        """
        return self.repository.search_symbols(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            limit=limit,
        )

    @need_index
    def search(
        self,
        query: str | Iterable[str],
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        limit: int = 20,
    ) -> list[Symbol]:
        """Найти символы по одному или нескольким запросам.

        Args:
            query: Один поисковый запрос или набор запросов.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            limit: Максимальное число результатов; в графе — ограничение ветвления.

        Returns:
            Объединённый список найденных символов.
        """
        return self.repository.search(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            limit=limit,
        )

    @need_index
    def search_page(
        self,
        query: str | Iterable[str],
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        limit: int = 20,
    ) -> Page:
        """Найти символы с информацией о продолжении выдачи.

        Args:
            query: Один поисковый запрос или набор запросов.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            limit: Максимальное число результатов; в графе — ограничение ветвления.

        Returns:
            Страница символов с признаками продолжения выдачи.
        """
        return self.repository.search_page(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            limit=limit,
        )

    @need_index
    def best_symbol(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
    ) -> Symbol:
        """Выбрать наиболее подходящий символ.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.

        Returns:
            Символ; при отсутствии совпадений возникает SymbolNotFoundError.
        """
        return self.repository.best_symbol(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
        )

    @need_index
    def symbol_context(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        limit: int = 20,
        anchors: bool = False,
        max_source_chars: int = 12000,
        ref_kinds: str | Iterable[str] | None = "behavioral",
    ) -> Inspection:
        """Получить определение символа, ссылки, реализации и импорты.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            limit: Максимальное число результатов; в графе — ограничение ветвления.
            anchors: Добавлять привязки к исходному коду.
            max_source_chars: Максимальное число символов исходного кода в привязке.
            ref_kinds: Виды ссылок: behavioral по умолчанию, None — все виды.

        Returns:
            Контекст символа с исходным кодом и признаками продолжения списков.
        """
        return self.repository.inspect(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            limit=limit,
            anchors=anchors,
            max_source_chars=max_source_chars,
            ref_kinds=ref_kinds,
        )

    @need_index
    def references(
        self,
        query: str,
        limit: int = 20,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        offset: int = 0,
        ref_kinds: str | Iterable[str] | None = "behavioral",
    ) -> Page:
        """Найти ссылки на символ.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            limit: Максимальное число результатов; в графе — ограничение ветвления.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            offset: Число пропускаемых ссылок или реализаций для пагинации.
            ref_kinds: Виды ссылок: behavioral по умолчанию, None — все виды.

        Returns:
            Страница ссылок с has_more и next_offset.
        """
        return self.repository.refs(
            query=query,
            limit=limit,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            offset=offset,
            ref_kinds=ref_kinds,
        )

    @need_index
    def implementations(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> Page:
        """Найти кандидатов в реализации символа.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            limit: Максимальное число результатов; в графе — ограничение ветвления.
            offset: Число пропускаемых ссылок или реализаций для пагинации.

        Returns:
            Страница кандидатов с has_more и next_offset.
        """
        return self.repository.impls(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            limit=limit,
            offset=offset,
        )

    @need_index
    def callers(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        depth: int = 3,
        limit: int = 20,
    ) -> CallGraph:
        """Построить граф вызывающих функций.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            depth: Глубина обхода графа; библиотека ограничивает допустимый диапазон.
            limit: Максимальное число результатов; в графе — ограничение ветвления.

        Returns:
            Граф входящих вызовов выбранного символа.
        """
        return self.repository.callers(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            depth=depth,
            limit=limit,
        )

    @need_index
    def callees(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        depth: int = 3,
        limit: int = 20,
        loose: bool = False,
    ) -> CallGraph:
        """Построить граф вызываемых функций.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            depth: Глубина обхода графа; библиотека ограничивает допустимый диапазон.
            limit: Максимальное число результатов; в графе — ограничение ветвления.
            loose: Включать менее строгие совпадения при разрешении исходящих вызовов.

        Returns:
            Граф исходящих вызовов выбранного символа.
        """
        return self.repository.callees(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            depth=depth,
            limit=limit,
            loose=loose,
        )

    @need_index
    def inspect_symbol(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        limit: int = 20,
        anchors: bool = False,
        max_source_chars: int = 12000,
    ) -> Inspection:
        """Получить определение символа и связанный контекст.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            limit: Максимальное число результатов; в графе — ограничение ветвления.
            anchors: Добавлять привязки к исходному коду.
            max_source_chars: Максимальное число символов исходного кода в привязке.

        Returns:
            Контекст символа с настройками ссылок по умолчанию библиотеки.
        """
        return self.repository.inspect_symbol(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            limit=limit,
            anchors=anchors,
            max_source_chars=max_source_chars,
        )

    @need_index
    def inspect_text(
        self,
        query: str,
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        options: InspectOptions | None = None,
        anchors: bool = False,
    ) -> str:
        """Представить контекст символа в текстовом формате.

        Args:
            query: Имя или идентификатор символа; поисковые методы также принимают текст запроса.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            options: Настройки состава и размера текстового контекста; None — стандартные.
            anchors: Добавлять привязки к исходному коду.

        Returns:
            Текст контекста, подготовленный библиотекой индекса.
        """
        return self.repository.inspect_text(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            options=options,
            anchors=anchors,
        )

    @need_index
    def search_text(
        self,
        query: str | Iterable[str],
        *,
        kind: str | Iterable[str] | None = None,
        language: str | None = None,
        path: str | Path | Iterable[str | Path] | None = None,
        exact_only: bool = False,
        limit: int = 20,
    ) -> str:
        """Представить результаты поиска в текстовом формате.

        Args:
            query: Один поисковый запрос или набор запросов.
            kind: Вид символа или набор видов; None отключает фильтр.
            language: Язык символа; None отключает фильтр.
            path: Путь или набор путей относительно корня репозитория для фильтрации.
            exact_only: Искать только точные совпадения.
            limit: Максимальное число результатов; в графе — ограничение ветвления.

        Returns:
            Текст найденных символов, подготовленный библиотекой индекса.
        """
        return self.repository.search_text(
            query=query,
            kind=kind,
            language=language,
            path=path,
            exact_only=exact_only,
            limit=limit,
        )

    @need_index
    def outline(
        self,
        path: str | Path,
        *,
        symbol: str | None = None,
        max_symbols: int = 200,
    ) -> Page:
        """Получить структуру символов файла.

        Args:
            path: Путь к файлу относительно корня репозитория или абсолютный путь внутри него.
            symbol: Локальный символ для сужения структуры файла; None — весь файл.
            max_symbols: Максимальное число символов в структуре файла.

        Returns:
            Страница символов файла.
        """
        return self.repository.outline(
            path=path,
            symbol=symbol,
            max_symbols=max_symbols,
        )

    @need_index
    def outline_text(
        self,
        path: str | Path,
        *,
        symbol: str | None = None,
        max_symbols: int = 200,
    ) -> str:
        """Представить структуру файла в текстовом формате.

        Args:
            path: Путь к файлу относительно корня репозитория или абсолютный путь внутри него.
            symbol: Локальный символ для сужения структуры файла; None — весь файл.
            max_symbols: Максимальное число символов в структуре файла.

        Returns:
            Текст структуры файла, подготовленный библиотекой индекса.
        """
        return self.repository.outline_text(
            path=path,
            symbol=symbol,
            max_symbols=max_symbols,
        )

    @need_index
    def read_file(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Прочитать UTF-8 файл целиком или выбранные строки.

        Args:
            path: Относительный путь от корня репозитория или абсолютный путь
                внутри него. Символические ссылки разрешаются перед проверкой.
            start_line: Первая строка, начиная с 1; None — начало файла.
            end_line: Последняя строка включительно; None — конец файла.
                Выход за конец файла обрезается до доступных строк.

        Returns:
            Строки без завершающего перевода строки; пустая строка для пустого
            файла или диапазона, начинающегося за концом файла.

        Raises:
            ValueError: Путь выходит за корень или диапазон строк некорректен.
            FileNotFoundError: Файл отсутствует или путь указывает на каталог.
            UnicodeDecodeError: Содержимое не является UTF-8 текстом.
            OSError: Файл недоступен для чтения.
        """
        root = self.repository.root.resolve()
        target = (root / path).resolve()

        if not target.is_relative_to(root):
            raise ValueError(f"Cannot read {path}: path outside repository")

        if not target.is_file():
            raise FileNotFoundError(path)

        start = 1 if start_line is None else start_line
        if start < 1 or (end_line is not None and end_line < start):
            raise ValueError(
                f"Cannot read {path}: invalid line range {start}:{end_line}"
            )

        lines = target.read_text(encoding="utf-8").splitlines()
        end = len(lines) if end_line is None else end_line

        return "\n".join(lines[start - 1 : end])

    def get_tree(self) -> str:
        """Вернуть текстовое дерево файлов репозитория из RepoIndex.

        Каталоги выводятся перед файлами согласно настройкам RepoIndex.
        Дерево файлов не ограничено языками индекса символов.
        """
        return self._repo_index.tree

    @property
    def repository(self) -> Repository:
        """Вернуть индекс символов, принадлежащий переданному RepoIndex."""
        return self._repo_index.index


def get_toolkit(repo_path: str, db_path: str) -> Toolkit:
    """Создать Toolkit после построения индекса репозитория.

    Args:
        repo_path: Корневой каталог репозитория.
        db_path: Путь к базе индекса.

    Returns:
        Новый Toolkit с RepoIndex, построенным индексом символов и деревом файлов.
    """
    repo_index = get_repo_index(repo_path, db_path)
    return Toolkit(repo_index)
