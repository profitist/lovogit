from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PackageFileDTO:
    filename: str
    url: str
    hashes: dict[str, str]
    requires_python: str | None
    yanked: bool
    yanked_reason: str | None
    size: int
    uploaded_at: datetime | None


@dataclass(frozen=True)
class PackageIndexDTO:
    name: str
    versions: tuple[str, ...]
    files: tuple[PackageFileDTO, ...]


@dataclass(frozen=True)
class PackageInfoDTO:
    name: str
    version: str
    summary: str | None
    description: str | None
    description_content_type: str | None
    requires_python: str | None
    requires_dist: tuple[str, ...]
    dynamic: tuple[str, ...]
    project_urls: dict[str, str]
    home_page: str | None
    package_url: str | None
    release_url: str | None
    yanked: bool
    yanked_reason: str | None
