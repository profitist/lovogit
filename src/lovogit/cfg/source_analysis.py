from pydantic import PositiveFloat, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceAnalysisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SOURCE_ANALYSIS_MAX_DOCUMENTS: PositiveInt = 10
    SOURCE_ANALYSIS_MAX_DOCUMENT_CHARS: PositiveInt = 60000
    SOURCE_ANALYSIS_MAX_TOTAL_CHARS: PositiveInt = 180000
    SOURCE_ANALYSIS_MAX_RESPONSE_CHARS: PositiveInt = 60000
    SOURCE_ANALYSIS_CALL_TIMEOUT: PositiveFloat = 30
    SOURCE_ANALYSIS_DEADLINE: PositiveFloat = 120
