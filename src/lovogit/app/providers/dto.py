"""Контракты получения исходников, независимые от анализа зависимостей."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoRefDTO:
    owner: str
    name: str
    repo_url: str


@dataclass(frozen=True)
class ResolvedRepoDTO:
    repo_id: int
    full_name: str
    repo_url: str
    default_branch: str
    commit_sha: str


@dataclass(frozen=True)
class SourceSnapshotDTO:
    repo: ResolvedRepoDTO
    root: Path
