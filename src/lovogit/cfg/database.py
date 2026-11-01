from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///db.sqlite3"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
