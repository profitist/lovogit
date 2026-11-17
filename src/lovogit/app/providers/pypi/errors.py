class PyPiError(Exception):
    """PyPI failure with request context, without response bodies or credentials."""

    def __init__(
        self,
        package: str,
        version: str | None,
        reason: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
    ):
        super().__init__(
            f"PyPI request failed for {package} version={version!r}: {reason}"
            f" (status_code={status_code})"
        )
        self.package = package
        self.version = version
        self.reason = reason
        self.status_code = status_code
        self.retry_after = retry_after


class PackageNotFound(PyPiError):
    pass


class PyPiUnavailable(PyPiError):
    pass


class PyPiRateLimited(PyPiError):
    pass


class InvalidPyPiResponse(PyPiError):
    pass
