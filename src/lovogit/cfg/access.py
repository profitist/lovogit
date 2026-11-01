from pydantic import Field, StrictInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotAccessSettings(BaseSettings):
    ALLOWED_CHAT_IDS: list[StrictInt] = Field(default_factory=list)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
