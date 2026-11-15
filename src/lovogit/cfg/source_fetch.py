from pydantic import Field, PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceFetchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SOURCE_FETCH_MAX_SOURCES: PositiveInt = 10
    SOURCE_FETCH_MAX_REDIRECTS: int = Field(default=5, ge=0)
    SOURCE_FETCH_CONNECT_TIMEOUT: PositiveFloat = 5
    SOURCE_FETCH_READ_TIMEOUT: PositiveFloat = 10
    SOURCE_FETCH_SOURCE_DEADLINE: PositiveFloat = 20
    SOURCE_FETCH_DEADLINE: PositiveFloat = 60
    SOURCE_FETCH_MAX_RESPONSE_BYTES: PositiveInt = 2 * 1024 * 1024
