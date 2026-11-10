from urllib.parse import urlsplit

import httpx

from lovogit.app.providers.errors import InvalidSourceResponse
from lovogit.cfg.pypi import PyPiSettings


class PyPiProvider:
    def __init__(self, client: httpx.AsyncClient, settings: PyPiSettings):
        self._client = client
        self._settings = settings

    def get_package(self):
        """Получение пакета"""
        pass

    def get_package_info(self):
        """Получение краткой инфы по пакету (версия, возможно release_note)"""
        pass

    def _request(self, url: str) -> httpx.Request:
        if not self._allowed_url(url):
            raise InvalidSourceResponse(stage="redirect")
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "LovoGit",
        }
        if urlsplit(url).netloc == "api.github.com":
            headers["Authorization"] = (
                "Bearer " + self._settings.PYPI_TOKEN.get_secret_value()
            )
        timeout = httpx.Timeout(
            self._settings.PYPY_READ_TIMEOUT,
            connect=self._settings.PYPI_CONNECT_TIMEOUT,
        )
        return httpx.Request(
            "GET", url, headers=headers, extensions={"timeout": timeout.as_dict()}
        )

    @staticmethod
    def _allowed_url(value: str) -> bool:
        try:
            url = urlsplit(value)
            return (
                url.scheme == "https"
                and url.netloc in {"api.github.com", "codeload.github.com"}
                and not url.fragment
            )
        except ValueError:
            return False
