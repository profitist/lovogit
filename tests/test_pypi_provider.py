import asyncio
import importlib
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from lovogit.app.providers.pypi import PyPiProvider
from lovogit.app.providers.pypi_errors import (
    InvalidPyPiResponse,
    PackageNotFound,
    PyPiRateLimited,
    PyPiUnavailable,
)
from lovogit.cfg.pypi import PyPiSettings


def metadata():
    return {
        "info": {
            "name": "Example_Pkg",
            "version": "2.0",
            "description": "Package README",
            "description_content_type": "text/markdown",
            "project_urls": {"Changelog": "https://example.org/changes"},
            "requires_dist": ["other>=1; python_version > '3.10'"],
            "requires_python": ">=3.11",
            "dynamic": ["requires_dist"],
        },
        "releases": "deprecated and deliberately ignored",
    }


def index():
    return {
        "meta": {"api-version": "1.4"},
        "name": "example-pkg",
        "versions": ["2.0", "1.0", "3.0rc1", "legacy"],
        "files": [
            {
                "filename": "example_pkg-2.0.tar.gz",
                "url": "../../files/pkg.tar.gz",
                "hashes": {"sha256": "a" * 64},
                "size": 123,
                "requires-python": ">=3.11",
                "yanked": "broken release",
                "upload-time": "2026-01-01T00:00:00Z",
                "future-field": {},
            }
        ],
    }


class PyPiProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.payload = metadata()
        self.status = 200
        self.headers = {}
        self.error = None
        self.delay = 0
        self.settings = PyPiSettings(_env_file=None)
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handle),
            headers={"Authorization": "secret", "Cookie": "secret"},
            auth=("user", "secret"),
            follow_redirects=True,
        )
        self.addAsyncCleanup(self.client.aclose)
        self.provider = PyPiProvider(self.client, self.settings)

    async def handle(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.delay:
            await asyncio.sleep(self.delay)
        headers = {"content-type": request.headers["accept"], **self.headers}
        if isinstance(self.payload, bytes):
            return httpx.Response(self.status, content=self.payload, headers=headers)
        return httpx.Response(self.status, json=self.payload, headers=headers)

    async def test_metadata_normalization_links_dependencies_and_client_ownership(self):
        result = await self.provider.get_package_info("Example.Pkg")
        self.assertEqual(result.name, "Example_Pkg")
        self.assertEqual(result.version, "2.0")
        self.assertEqual(result.description, "Package README")
        self.assertEqual(
            result.project_urls["Changelog"], "https://example.org/changes"
        )
        self.assertEqual(result.requires_dist, ("other>=1; python_version > '3.10'",))
        self.assertEqual(result.dynamic, ("requires_dist",))
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(
            str(self.requests[0].url), "https://pypi.org/pypi/example-pkg/json"
        )
        self.assertFalse(self.client.is_closed)

    async def test_explicit_release_and_no_releases_key(self):
        del self.payload["releases"]
        self.payload["info"]["version"] = "2.0+local"
        self.payload["info"]["yanked"] = True
        self.payload["info"]["yanked_reason"] = "withdrawn"
        result = await self.provider.get_package_info("example-pkg", "2.0+local")
        self.assertTrue(result.yanked)
        self.assertEqual(result.yanked_reason, "withdrawn")
        self.assertEqual(self.requests[0].url.path, "/pypi/example-pkg/2.0+local/json")

    async def test_nullable_metadata_and_future_fields(self):
        self.payload = {
            "info": {
                "name": "example-pkg",
                "version": "1",
                "requires_dist": None,
                "project_urls": None,
                "future": [1],
            }
        }
        result = await self.provider.get_package_info("example-pkg")
        self.assertEqual(result.requires_dist, ())
        self.assertEqual(result.project_urls, {})
        self.assertIsNone(result.description)

    async def test_index_preserves_versions_and_distribution_details(self):
        self.payload = index()
        result = await self.provider.get_package("example-pkg")
        self.assertEqual(result.versions, ("2.0", "1.0", "3.0rc1", "legacy"))
        file = result.files[0]
        self.assertEqual(file.url, "https://pypi.org/files/pkg.tar.gz")
        self.assertTrue(file.yanked)
        self.assertEqual(file.yanked_reason, "broken release")
        self.assertEqual(file.hashes["sha256"], "a" * 64)
        self.assertEqual(file.requires_python, ">=3.11")
        self.assertEqual(file.uploaded_at.utcoffset().total_seconds(), 0)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(
            self.requests[0].headers["accept"], "application/vnd.pypi.simple.v1+json"
        )

    async def test_index_empty_and_yank_variants(self):
        for yanked, expected, reason in [
            (False, False, None),
            (True, True, None),
            ("", True, ""),
            ("broken release", True, "broken release"),
        ]:
            with self.subTest(yanked=yanked):
                self.payload = index()
                self.payload["files"][0]["yanked"] = yanked
                result = await self.provider.get_package("example-pkg")
                self.assertEqual(result.files[0].yanked, expected)
                self.assertEqual(result.files[0].yanked_reason, reason)
        self.payload = index()
        self.payload.update(files=[], versions=[])
        result = await self.provider.get_package("example-pkg")
        self.assertEqual(result.files, ())
        self.assertEqual(result.versions, ())

    async def test_never_sends_client_credentials(self):
        await self.provider.get_package_info("example-pkg")
        request = self.requests[0]
        self.assertNotIn("authorization", request.headers)
        self.assertNotIn("cookie", request.headers)
        self.assertEqual(request.headers["user-agent"], "LovoGit")
        self.assertEqual(request.extensions["timeout"]["connect"], 10)

    async def test_invalid_inputs_make_no_request(self):
        for name in [
            "",
            "../other",
            "a/b",
            "a?x",
            "é",
            "a\n",
            "-a",
            "a-",
            "pkg[extra]",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                await self.provider.get_package(name)
        for version in ["", "../2", "2/3", "2?token=secret", "2\n"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                await self.provider.get_package_info("example-pkg", version)
        self.assertEqual(self.requests, [])

    async def test_status_errors_keep_context_without_response_body(self):
        for code, error in [
            (404, PackageNotFound),
            (429, PyPiRateLimited),
            (500, PyPiUnavailable),
            (503, PyPiUnavailable),
            (401, InvalidPyPiResponse),
            (403, InvalidPyPiResponse),
        ]:
            self.status = code
            self.payload = b"private-body"
            self.headers = {"retry-after": "60"}
            with self.subTest(code=code), self.assertRaises(error) as caught:
                await self.provider.get_package_info("example-pkg", "1")
            self.assertEqual(caught.exception.package, "example-pkg")
            self.assertEqual(caught.exception.version, "1")
            self.assertEqual(caught.exception.status_code, code)
            self.assertNotIn("private-body", str(caught.exception))
            if code == 429:
                self.assertEqual(caught.exception.retry_after, "60")

    async def test_redirect_is_not_followed(self):
        self.status = 302
        self.headers = {"location": "https://other.example/steal"}
        with self.assertRaises(InvalidPyPiResponse):
            await self.provider.get_package_info("example-pkg")
        self.assertEqual(len(self.requests), 1)

    async def test_transport_and_deadline(self):
        self.error = httpx.ReadTimeout(
            "secret", request=httpx.Request("GET", "https://pypi.org")
        )
        with self.assertRaises(PyPiUnavailable) as caught:
            await self.provider.get_package_info("example-pkg")
        self.assertNotIn("secret", str(caught.exception))
        self.error = None
        self.delay = 0.05
        provider = PyPiProvider(
            self.client, self.settings.model_copy(update={"PYPI_DEADLINE": 0.001})
        )
        with self.assertRaises(PyPiUnavailable):
            await provider.get_package_info("example-pkg")

    async def test_invalid_json_schema_and_identity(self):
        for payload in [
            b"not-json",
            [],
            {},
            {"info": None},
            {"info": {"name": "example-pkg", "version": 123}},
            {"info": {"name": "other", "version": "1"}},
        ]:
            self.payload = payload
            with self.subTest(payload=payload), self.assertRaises(InvalidPyPiResponse):
                await self.provider.get_package_info("example-pkg")

    async def test_index_schema_and_api_version(self):
        for api_version in ["2.0", "1.0", "invalid"]:
            self.payload = index()
            self.payload["meta"]["api-version"] = api_version
            with (
                self.subTest(api_version=api_version),
                self.assertRaises(InvalidPyPiResponse),
            ):
                await self.provider.get_package("example-pkg")
        self.payload = index()
        self.payload["files"][0]["size"] = "123"
        with self.assertRaises(InvalidPyPiResponse):
            await self.provider.get_package("example-pkg")

    async def test_content_type_and_size(self):
        self.headers = {"content-type": "text/html"}
        with self.assertRaises(InvalidPyPiResponse):
            await self.provider.get_package_info("example-pkg")
        self.headers = {"content-type": "application/json; charset=utf-8"}
        await self.provider.get_package_info("example-pkg")
        provider = PyPiProvider(
            self.client,
            self.settings.model_copy(update={"PYPI_MAX_RESPONSE_BYTES": 10}),
        )
        with self.assertRaises(InvalidPyPiResponse):
            await provider.get_package_info("example-pkg")

    async def test_cancel_propagates(self):
        self.error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.provider.get_package_info("example-pkg")

    async def test_stream_closed_on_success_status_size_and_transport_errors(self):
        class ResponseStream(httpx.AsyncByteStream):
            def __init__(self, content, error=None):
                self.content = content
                self.error = error
                self.closed = False

            async def __aiter__(self):
                yield self.content
                if self.error:
                    raise self.error

            async def aclose(self):
                self.closed = True

        cases = [
            (200, b'{"info":{"name":"example-pkg","version":"1"}}', None, None),
            (404, b"", None, PackageNotFound),
            (200, b"x" * 200, None, InvalidPyPiResponse),
            (200, b"{}", httpx.ReadError("broken stream"), PyPiUnavailable),
        ]
        for status, content, error, expected in cases:
            with self.subTest(status=status, error=expected):
                stream = ResponseStream(content, error)
                response = httpx.Response(
                    status, stream=stream, headers={"content-type": "application/json"}
                )
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda request, result=response: result
                    )
                ) as client:
                    provider = PyPiProvider(
                        client,
                        self.settings.model_copy(
                            update={"PYPI_MAX_RESPONSE_BYTES": 100}
                        ),
                    )
                    if expected:
                        with self.assertRaises(expected):
                            await provider.get_package_info("example-pkg")
                    else:
                        await provider.get_package_info("example-pkg")
                    self.assertTrue(stream.closed)
                    self.assertFalse(client.is_closed)


class PyPiSettingsTests(unittest.TestCase):
    def test_independent_defaults_and_legacy_timeout_name(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(PyPiSettings(_env_file=None).PYPI_READ_TIMEOUT, 30)
            self.assertEqual(
                PyPiSettings(_env_file=None, PYPY_READ_TIMEOUT=7).PYPI_READ_TIMEOUT, 7
            )
            importlib.reload(importlib.import_module("lovogit.app.providers.pypi"))

    def test_invalid_configuration(self):
        for url in [
            "http://pypi.org",
            "https://user:pass@pypi.org",
            "https://pypi.org/path",
            "https://pypi.org?token=secret",
            "https://pypi.org/#fragment",
        ]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                PyPiSettings(_env_file=None, API_URL=url)
        for field in [
            "PYPI_READ_TIMEOUT",
            "PYPI_CONNECT_TIMEOUT",
            "PYPI_DEADLINE",
            "PYPI_MAX_RESPONSE_BYTES",
        ]:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                PyPiSettings(_env_file=None, **{field: 0})
