from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RepoRequestDTO:
    repo_id: str | None
    repo_name: str | None
    repo_url: str | None


@dataclass
class RepoResponseDTO:
    # TODO поменять контракт
    repo_id: str
    repo_name: str
    repo_url: str
    release_version: str | None


@dataclass
class DependenciesResponseDTO(RepoResponseDTO):
    # TODO поменять контракт
    deps: list[DependencyDTO]


@dataclass
class DependencyDTO:
    current_version: str
    name: str


class BaseRepoProvider(ABC):

    @abstractmethod
    async def get_repo(self, repo: RepoRequestDTO) -> RepoResponseDTO:
        pass

    @abstractmethod
    async def download_repo(self, repo_url):
        pass

    @abstractmethod
    async def get_deps(self, repo: RepoRequestDTO) -> DependenciesResponseDTO:
        pass