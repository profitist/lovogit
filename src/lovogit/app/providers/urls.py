import re
from urllib.parse import urlsplit

from .dto import RepoRefDTO
from .errors import InvalidRepoURL


def parse_github_url(value: str) -> RepoRefDTO:
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.netloc.lower() != "github.com"
            or "?" in value
            or "#" in value
            or any(c.isspace() for c in value)
        ):
            raise ValueError
        parts = url.path.removesuffix("/").split("/")
        if len(parts) != 3 or parts[0]:
            raise ValueError
        owner, name = parts[1], parts[2].removesuffix(".git")
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
            or name in {".", ".."}
        ):
            raise ValueError
        return RepoRefDTO(owner, name, f"https://github.com/{owner}/{name}")
    except ValueError, TypeError:
        raise InvalidRepoURL() from None
