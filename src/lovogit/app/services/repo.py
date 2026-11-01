import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from lovogit.app.providers import BaseRepoProvider, RepoRequestDTO
from lovogit.app.providers.errors import RepoNotFound
from lovogit.db import RepoModel, DependencyModel


logger = logging.getLogger(__name__)

@dataclass
class RepoServiceDTO:
    repo_name: str | None
    repo_url: str | None
    repo_id: str | None

    def to_request(self) -> RepoRequestDTO:
        return RepoRequestDTO(repo_name=self.repo_name, repo_url=self.repo_url, repo_id=self.repo_id)

class RepoService:

    def __init__(self, provider: BaseRepoProvider, session: AsyncSession):
        self.provider = provider
        self.session = session

    async def init_repo(self, repo: RepoServiceDTO):
        try:
            founded_repo = await self.provider.get_repo(repo.to_request())
        except RepoNotFound:
            logger.error("Repo %s Wasn't Found", repo.repo_name)
            raise RepoNotFound("Repo %s Wasn't Found" % repo.repo_name)
        repo_db = RepoModel(
            name=founded_repo.repo_name,
            repo_url=founded_repo.repo_url,
        )
        deps_dtp = await self.provider.get_deps(repo.to_request())

        repo_db.dependencies = []
        for dep in deps_dtp.deps:
            dependency_db = DependencyModel(
                name=dep.name,
                current_version=dep.current_version,
            )
            repo_db.dependencies.append(dependency_db)

        self.session.add(repo_db)
        await self.session.commit()


# TS-01a192c9-eccd-7004-a507-49c05fb0f029
