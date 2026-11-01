"""Не пропускать сырые HTTP-трассы GitHub в журнал приложения."""

import logging
from contextvars import ContextVar

private_request = ContextVar("github_private_request", default=False)


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record):
        return not private_request.get()


for name in (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    logging.getLogger(name).addFilter(_PrivateRequestFilter())
