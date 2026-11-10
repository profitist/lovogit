from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings


class PyPiSettings(BaseSettings):
    """Конфигурация Pypi клиента"""

    API_URL: HttpUrl = "https://pypi.org"
    PYPY_READ_TIMEOUT: int = 60
    PYPI_CONNECT_TIMEOUT: int = 60

    PYPI_TOKEN: SecretStr = SecretStr("...")
