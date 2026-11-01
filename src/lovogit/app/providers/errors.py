"""Безопасные ошибки получения исходников без HTTP bodies и секретов."""

from datetime import datetime


class SourceError(Exception):
    _message = "Не удалось получить исходники"

    def __init__(self, *, stage: str = "input", status_code: int | None = None):
        context = f"stage={stage}"
        if status_code is not None:
            context += f", status_code={status_code}"
        super().__init__(f"{self._message} ({context})")
        self.stage = stage
        self.status_code = status_code

    @property
    def user_message(self) -> str:
        return self._message


class InvalidRepoURL(SourceError):
    _message = "Требуется HTTPS URL корня репозитория github.com"


class AuthenticationError(SourceError):
    _message = "GitHub-токен недействителен"


class RepoNotFound(SourceError):
    _message = "Репозиторий не найден или недоступен"


class RevisionUnavailable(SourceError):
    _message = "Ревизия репозитория недоступна"


class RateLimited(SourceError):
    _message = "Достигнут лимит запросов GitHub"

    def __init__(self, *, retry_at: datetime | None = None, **kwargs):
        super().__init__(**kwargs)
        self.retry_at = retry_at


class SourceUnavailable(SourceError):
    _message = "GitHub временно недоступен"


class InvalidSourceResponse(SourceError):
    _message = "Некорректный ответ GitHub"


class InvalidArchive(SourceError):
    _message = "Архив исходников повреждён или превышает ограничения"


class SourceStorageError(SourceError):
    _message = "Не удалось сохранить исходники локально"
