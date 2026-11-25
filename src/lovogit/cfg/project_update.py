from pydantic import PositiveFloat, PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProjectUpdateSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    UPDATE_DEADLINE: PositiveFloat = 600
    UPDATE_MAX_CHANGES: PositiveInt = 10
    UPDATE_MODEL_CALLS: PositiveInt = 12
    UPDATE_TOOL_CALLS: PositiveInt = 24
    UPDATE_COMPARE_TIMEOUT: PositiveFloat = 60
    UPDATE_MAX_TOKENS: PositiveInt = 5000
    UPDATE_TRACE_BYTES: PositiveInt = 20 * 1024 * 1024
    UPDATE_TOOL_CHARS: PositiveInt = 30000
    UPDATE_SNIPPET_LINES: PositiveInt = 20
    UPDATE_SNIPPET_CHARS: PositiveInt = 8000


class UpdateModelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    LLM_PROVIDER: str = "openai_compat"
    LLM_API_KEY: SecretStr
    LLM_BASE_URL: str
    LLM_REASONING_EFFORT: str = ""
    MODEL_CHEAP: str
    MODEL_STRONG: str

    @field_validator("LLM_PROVIDER")
    @classmethod
    def _supported_provider(cls, value: str) -> str:
        if value != "openai_compat":
            raise ValueError("Manual analysis supports LLM_PROVIDER=openai_compat")
        return value

    @field_validator("LLM_API_KEY")
    @classmethod
    def _nonempty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Manual analysis requires LLM_API_KEY")
        return value


class TraceExportSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    LANGFUSE_HOST: str
    LANGFUSE_PUBLIC_KEY: SecretStr
    LANGFUSE_SECRET_KEY: SecretStr
