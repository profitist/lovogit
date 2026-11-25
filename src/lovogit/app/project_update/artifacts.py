import json
import re
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

from packaging.utils import canonicalize_name


def json_value(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def create_run_directory(checkout: Path, package: str) -> Path:
    name = canonicalize_name(package, validate=True)
    name = re.sub(r"[^a-z0-9-]", "-", name)[:80]
    root = checkout / "artifacts" / "runs"
    root.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + name + "-"
    return Path(mkdtemp(prefix=prefix, dir=root))
