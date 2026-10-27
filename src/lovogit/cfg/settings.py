from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Settings(BaseSettings):
    """Настройки приложения"""

    # Telegram bot
    BOT_TOKEN: SecretStr

    # LLM
    LLM_API_KEY: SecretStr
    LLM_PROVIDER: str
    LLM_BASE_URL: str
    MODEL_CHEAP: str
    MODEL_STRONG: str

    model_config = SettingsConfigDict(env_file=".env")


settings = _Settings()
