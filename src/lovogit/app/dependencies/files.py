import hashlib
import os
import stat
from pathlib import Path

from lovogit.app.dependencies.dto import InventoryLimits, ProvenanceDTO, ReadFileDTO


class InventoryFileError(Exception):
    def __init__(self, code: str, origin: ProvenanceDTO):
        self.code = code
        self.origin = origin
        super().__init__(f"Cannot read dependency source {origin.path!r}: {code}")


class SnapshotReader:
    def __init__(self, root: Path, limits: InventoryLimits):
        if root.is_symlink() or not root.is_dir():
            raise ValueError(
                "Dependency inventory requires a regular snapshot directory"
            )
        self._root = root.resolve()
        self._limits = limits
        self._cache: dict[str, bytes] = {}
        self._files: list[ReadFileDTO] = []
        self._total_bytes = 0
        self._records = 0
        self._exhausted = False

    @property
    def files(self) -> tuple[ReadFileDTO, ...]:
        return tuple(sorted(self._files, key=lambda item: item.path))

    def locate(self, value: str, origin: ProvenanceDTO, *, parent: str = "") -> str:
        if (
            not value
            or ":" in value
            or "\\" in value
            or "\x00" in value
            or Path(value).is_absolute()
        ):
            raise InventoryFileError("forbidden_path", origin)
        relative = os.path.normpath(
            str(Path(parent).parent / value) if parent else value
        )
        if relative == ".." or relative.startswith("../") or relative == ".":
            raise InventoryFileError("outside_snapshot", origin)
        target = self._root
        try:
            for part in Path(relative).parts:
                target = target / part
                if target.is_symlink():
                    raise InventoryFileError("symlink", origin)
        except OSError:
            raise InventoryFileError("unreadable_file", origin) from None
        return relative

    def exists(self, value: str) -> bool:
        try:
            (self._root / value).lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise InventoryFileError("unreadable_file", ProvenanceDTO(value)) from None
        return True

    def read(self, value: str, origin: ProvenanceDTO) -> bytes:
        if self._exhausted:
            raise InventoryFileError("budget_exhausted", origin)
        relative = self.locate(value, origin)
        if relative in self._cache:
            return self._cache[relative]
        if len(self._cache) >= self._limits.max_files:
            self._exhausted = True
            raise InventoryFileError("file_count_limit", origin)
        try:
            descriptor = os.open(
                self._root / relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise InventoryFileError("special_file", origin)
                if info.st_size > self._limits.max_file_bytes:
                    self._exhausted = True
                    raise InventoryFileError("file_size_limit", origin)
                remaining = self._limits.max_total_bytes - self._total_bytes
                if info.st_size > remaining:
                    self._exhausted = True
                    raise InventoryFileError("total_size_limit", origin)
                data = stream.read(min(self._limits.max_file_bytes, remaining) + 1)
                if len(data) > min(self._limits.max_file_bytes, remaining):
                    self._exhausted = True
                    raise InventoryFileError("file_changed_or_size_limit", origin)
        except FileNotFoundError:
            raise InventoryFileError("missing_file", origin) from None
        except OSError:
            raise InventoryFileError("unreadable_file", origin) from None
        self._cache[relative] = data
        self._total_bytes += len(data)
        self._files.append(
            ReadFileDTO(relative, hashlib.sha256(data).hexdigest(), len(data))
        )
        return data

    def consume_records(
        self, count: int, origin: ProvenanceDTO, *, depth: int = 0
    ) -> None:
        if self._exhausted:
            raise InventoryFileError("budget_exhausted", origin)
        if depth > self._limits.max_depth:
            self._exhausted = True
            raise InventoryFileError("include_depth_limit", origin)
        self._records += count
        if self._records > self._limits.max_records:
            self._exhausted = True
            raise InventoryFileError("record_limit", origin)
