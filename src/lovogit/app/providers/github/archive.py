"""Ограниченная распаковка ZIP; вызывается вне event loop."""

import stat
import zlib
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from .errors import InvalidArchive


def extract_archive(archive: Path, root: Path, max_bytes: int, max_entries: int):
    try:
        with ZipFile(archive) as zipped:
            entries = zipped.infolist()
            if not entries or len(entries) > max_entries:
                raise ValueError
            prefix = None
            seen = set()
            total = 0
            for entry in entries:
                name = entry.orig_filename
                parts = name.removesuffix("/").split("/")
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (
                    not name
                    or "\\" in name
                    or "\x00" in name
                    or ":" in name
                    or any(part in {"", ".", ".."} for part in parts)
                    or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or (kind == stat.S_IFDIR and not entry.is_dir())
                    or (kind == stat.S_IFREG and entry.is_dir())
                ):
                    raise ValueError
                if prefix is None:
                    prefix = parts[0]
                if parts[0] != prefix or (len(parts) == 1 and not entry.is_dir()):
                    raise ValueError
                relative = Path(*parts[1:])
                if relative in seen:
                    raise ValueError
                seen.add(relative)
                target = root / relative
                if not target.resolve().is_relative_to(root.resolve()):
                    raise ValueError
                if entry.is_dir():
                    if target.exists() and not target.is_dir():
                        raise ValueError
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if target.exists() or any(
                    p.is_file() for p in target.parents if p != root
                ):
                    raise ValueError
                if total + entry.file_size > max_bytes:
                    raise ValueError
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as source, target.open("xb") as output:
                    while chunk := source.read(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ValueError
                        output.write(chunk)
    except (
        BadZipFile,
        ValueError,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ):
        raise InvalidArchive(stage="extract") from None
