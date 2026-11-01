from pydantic import SecretStr

from .access import BotAccessSettings


class _Settings(BotAccessSettings):
    """Настройки приложения"""

    # Telegram bot
    BOT_TOKEN: SecretStr

    # LLM
    LLM_API_KEY: SecretStr
    LLM_PROVIDER: str
    LLM_BASE_URL: str
    MODEL_CHEAP: str
    MODEL_STRONG: str


settings = _Settings()
