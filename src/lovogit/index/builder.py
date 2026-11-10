from code_symbol_index import Repository
from pyletree import FileTree


class RepoIndex:
    """Хранить индекс Python-символов и представление дерева репозитория."""

    def __init__(self, path: str, db_path: str):
        """Подготовить хранилище индекса и дерево без сканирования символов.

        Args:
            path: Корневой каталог репозитория.
            db_path: Путь к базе индекса; отсутствующая база будет создана.
        """
        self._repo = Repository(
            root=path, db_path=db_path, languages=["python"], create_index=True
        )
        self._tree = FileTree(path, dirs_first=True)

    def build(self) -> None:
        """Полностью перестроить индекс Python-символов репозитория."""
        self._repo.build()

    def refresh(self) -> None:
        """Обновить индекс с учётом изменённых и удалённых файлов."""
        self._repo.refresh()

    @property
    def index(self) -> Repository:
        """Вернуть индекс символов для поисковых операций."""
        return self._repo

    @property
    def tree(self) -> str:
        """Получить дерево файлов с каталогами перед файлами без фильтра по языку."""
        return self._tree.get_tree()


def get_repo_index(path: str, db_path: str) -> RepoIndex:
    """Создать RepoIndex и полностью построить его индекс символов.

    Args:
        path: Корневой каталог репозитория.
        db_path: Путь к создаваемой или существующей базе индекса.

    Returns:
        Новый RepoIndex с построенным индексом символов и деревом файлов.
    """
    idx = RepoIndex(path, db_path)
    idx.build()
    return idx
