from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lovogit.app.providers.github.client import GitHubProvider
from lovogit.app.providers.github.dto import ResolvedRepoDTO
from lovogit.app.providers.github.errors import InvalidRepoURL
from lovogit.app.providers.github.urls import parse_github_url
from lovogit.db import RepoModel


class RegistrationUnavailable(Exception):
    """Не удалось сохранить регистрацию репозитория."""


class RegistrationConflict(Exception):
    """Существующие записи противоречат идентичности GitHub-репозитория."""


class RegistrationOutcome(StrEnum):
    ADDED = "added"
    ALREADY_TRACKED = "already_tracked"
    RESTORED = "restored"


@dataclass(frozen=True)
class RegisteredRepositoryDTO:
    project_id: int
    project_name: str
    repo_url: str
    outcome: RegistrationOutcome


class RepositoryRegistrationService:
    def __init__(
        self, provider: GitHubProvider, sessions: async_sessionmaker[AsyncSession]
    ):
        self._provider = provider
        self._sessions = sessions

    async def add_repository(self, github_url: str) -> RegisteredRepositoryDTO:
        ref = parse_github_url(github_url)
        resolved = await self._provider.resolve(ref.repo_url)
        urls = {ref.repo_url.lower(), resolved.repo_url.lower()}
        try:
            try:
                return await self._save(resolved, urls)
            except IntegrityError as error:
                if (
                    getattr(error.orig, "sqlite_errorname", None)
                    != "SQLITE_CONSTRAINT_UNIQUE"
                    or str(error.orig) != "UNIQUE constraint failed: repos.github_id"
                ):
                    raise
                return await self._save(resolved, urls)
        except SQLAlchemyError as error:
            raise RegistrationUnavailable(
                f"Не удалось сохранить регистрацию GitHub ID {resolved.repo_id}"
            ) from error

    async def _save(
        self, resolved: ResolvedRepoDTO, urls: set[str]
    ) -> RegisteredRepositoryDTO:
        async with self._sessions.begin() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            repos = (await session.scalars(select(RepoModel))).all()
            candidates = []
            for repo in repos:
                try:
                    same_url = parse_github_url(repo.repo_url).repo_url.lower() in urls
                except InvalidRepoURL:
                    same_url = False
                if repo.github_id == resolved.repo_id or same_url:
                    candidates.append(repo)
            if len(candidates) > 1 or (
                candidates and candidates[0].github_id not in (None, resolved.repo_id)
            ):
                raise RegistrationConflict(
                    f"Конфликт регистрации GitHub ID {resolved.repo_id}: "
                    f"локальные ID {[repo.id for repo in candidates]}"
                )
            if candidates:
                repo = candidates[0]
                outcome = (
                    RegistrationOutcome.RESTORED
                    if repo.deleted_at is not None
                    else RegistrationOutcome.ALREADY_TRACKED
                )
            else:
                repo = RepoModel()
                session.add(repo)
                outcome = RegistrationOutcome.ADDED
            repo.github_id = resolved.repo_id
            repo.name = resolved.full_name
            repo.repo_url = resolved.repo_url
            repo.deleted_at = None
            await session.flush()
            result = RegisteredRepositoryDTO(repo.id, repo.name, repo.repo_url, outcome)
        return result
