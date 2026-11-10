from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    Field,
    HttpUrl,
    PositiveFloat,
    PositiveInt,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class PyPiSettings(BaseSettings):
    """Public PyPI API settings; reads do not require authentication."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    API_URL: HttpUrl = "https://pypi.org"
    PYPI_READ_TIMEOUT: PositiveFloat = Field(
        default=30,
        validation_alias=AliasChoices("PYPI_READ_TIMEOUT", "PYPY_READ_TIMEOUT"),
    )
    PYPI_CONNECT_TIMEOUT: PositiveFloat = 10
    PYPI_DEADLINE: PositiveFloat = 60
    PYPI_MAX_RESPONSE_BYTES: PositiveInt = 64 * 1024 * 1024

    @field_validator("API_URL")
    @classmethod
    def _api_origin(cls, value: HttpUrl) -> HttpUrl:
        url = urlsplit(str(value))
        if (
            url.scheme != "https"
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("PyPI API_URL must be an HTTPS origin without credentials")
        return value
