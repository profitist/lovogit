"""GitHub настраивается независимо от бота и LLM."""

from pathlib import Path

from pydantic import PositiveFloat, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GitHubSettings(BaseSettings):
    GITHUB_TOKEN: SecretStr
    GITHUB_WORKSPACE_DIR: Path | None = None
    GITHUB_CONNECT_TIMEOUT: PositiveFloat = 10
    GITHUB_READ_TIMEOUT: PositiveFloat = 30
    GITHUB_DEADLINE: PositiveFloat = 120
    GITHUB_MAX_ARCHIVE_BYTES: PositiveInt = 100 * 1024 * 1024
    GITHUB_MAX_EXTRACTED_BYTES: PositiveInt = 500 * 1024 * 1024
    GITHUB_MAX_ENTRIES: PositiveInt = 50_000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("GITHUB_TOKEN")
    @classmethod
    def _nonempty_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Требуется GitHub-токен")
        return value
