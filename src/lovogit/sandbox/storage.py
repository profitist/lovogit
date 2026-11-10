import asyncio
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path, PurePosixPath
from uuid import uuid4


class SandboxError(RuntimeError):
    pass


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def safe_path(path: str) -> str:
    parts = PurePosixPath(path).parts
    if (
        not parts
        or path.startswith(("/", "-"))
        or any(p in {"", ".", ".."} for p in path.split("/"))
        or any(c in path for c in ("\\", "\x00", ":", "\n", "\r"))
    ):
        raise ValueError(f"Sandbox path is invalid: {path!r}")
    return str(PurePosixPath(path))


class ArtifactStore:
    def __init__(self, root: Path, max_bytes: int):
        self._root = root.resolve()
        self._max_bytes = max_bytes
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @asynccontextmanager
    async def lock(self):
        with (self._root / "operation.lock").open("a") as handle:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def path(self, group: str, identifier: str) -> Path:
        if not re.fullmatch(r"[a-z_]+", group) or not re.fullmatch(
            r"[a-f0-9]{32,64}", identifier
        ):
            raise ValueError(f"Invalid sandbox identifier: {identifier!r}")
        result = self._root / group / identifier
        result.parent.mkdir(parents=True, exist_ok=True)
        return result

    def save(self, group: str, identifier: str, data: dict) -> None:
        destination = self.path(group, identifier)
        self._persist(destination, json.dumps(data, sort_keys=True).encode())

    def load(self, group: str, identifier: str) -> dict:
        try:
            return json.loads(self.path(group, identifier).read_text())
        except FileNotFoundError as exc:
            raise SandboxError(f"Sandbox {group} {identifier} does not exist") from exc

    def artifact(self, data: bytes) -> str:
        if len(data) > self._max_bytes:
            raise SandboxError("Sandbox artifact exceeds configured size limit")
        identifier = hashlib.sha256(data).hexdigest()
        destination = self.path("artifacts", identifier)
        if not destination.exists():
            self._persist(destination, data)
        return identifier

    def read(self, identifier: str) -> bytes:
        try:
            return self.path("artifacts", identifier).read_bytes()
        except FileNotFoundError as exc:
            raise SandboxError(f"Sandbox artifact {identifier} does not exist") from exc

    def _persist(self, destination: Path, data: bytes) -> None:
        temporary = destination.with_suffix(f".{uuid4().hex}.tmp")
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)


def snapshot_repository(
    source: Path, destination: Path, max_bytes: int
) -> tuple[str, str | None]:
    excluded = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".ssh",
        ".aws",
    }
    hashes = {}
    size = 0
    destination.mkdir(parents=True)
    try:
        for directory, names, files in os.walk(source, followlinks=False):
            names[:] = sorted(
                n
                for n in names
                if n not in excluded and n != ".env" and not n.startswith(".env.")
            )
            for name in names + sorted(files):
                path = Path(directory) / name
                if name == ".env" or name.startswith(".env."):
                    continue
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise SandboxError(
                        f"Repository snapshot rejects special file: {path.relative_to(source)}"
                    )
                if stat.S_ISDIR(mode):
                    continue
                relative = safe_path(path.relative_to(source).as_posix())
                with path.open("rb") as stream:
                    data = stream.read(max_bytes - size + 1)
                size += len(data)
                if size > max_bytes or len(hashes) >= 20000:
                    raise SandboxError("Repository snapshot exceeds configured limit")
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o755 if mode & 0o111 else 0o644)
                hashes[relative] = hashlib.sha256(data).hexdigest()
    except BaseException:
        shutil.rmtree(destination)
        raise
    lock_hash = fingerprint(
        {
            p: h
            for p, h in hashes.items()
            if p in {"uv.lock", "poetry.lock", "requirements.txt", "pyproject.toml"}
        }
    )
    return fingerprint(hashes), lock_hash


def repository_tar(root: Path) -> bytes:
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path in sorted(root.rglob("*")):
            entry = archive.gettarinfo(str(path), path.relative_to(root).as_posix())
            entry.uid = entry.gid = 1000
            entry.uname = entry.gname = "sandbox"
            if entry.isfile():
                with path.open("rb") as stream:
                    archive.addfile(entry, stream)
            else:
                archive.addfile(entry)
    return output.getvalue()
