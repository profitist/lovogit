"""Доступ к исходникам без регистрации проекта и изменения инвентаря."""

from contextlib import asynccontextmanager

from lovogit.app.providers.github.client import GitHubProvider


class RepoService:
    def __init__(self, provider: GitHubProvider):
        self._provider = provider

    @asynccontextmanager
    async def open_source(self, github_url: str):
        repo = await self._provider.resolve(github_url)
        async with self._provider.snapshot(repo) as snapshot:
            yield snapshot
