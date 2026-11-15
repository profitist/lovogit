import asyncio
import unittest
from unittest.mock import AsyncMock

import httpcore
import httpx

from lovogit.app.providers.source_document import SourceDocumentLoader
from lovogit.app.providers.source_document_transport import (
    CheckedNetworkBackend,
    SourceDocumentTransport,
)
from lovogit.cfg.source_fetch import SourceFetchSettings


class Body(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    async def __aiter__(self):
        yield self.data

    async def aclose(self):
        self.closed = True


class SourceDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_httpcore_response_is_transport_error(self):
        stream = httpcore.AsyncMockStream(
            [
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 9\r\n\r\nshort",
                b"",
            ]
        )
        backend = AsyncMock()
        backend.connect_tcp.return_value = stream
        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None),
            SourceDocumentTransport(
                CheckedNetworkBackend(backend, AsyncMock(return_value=["8.8.8.8"]))
            ),
        )
        try:
            result = await loader.fetch("http://example.com/")
        finally:
            await loader.aclose()
        self.assertEqual(result.reason, "transport_error")
        self.assertIsNone(result.text)

    async def test_long_content_length_with_leading_zeroes(self):
        stream = Body(b"x")
        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None),
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={
                        "content-type": "text/plain",
                        "content-length": "0" * 5000 + "1",
                    },
                    stream=stream,
                )
            ),
        )
        try:
            result = await loader.fetch("https://example.com/")
        finally:
            await loader.aclose()
        self.assertEqual(result.text, "x")

    async def test_text_formats_and_http_statuses(self):
        cases = [
            (200, "text/plain; charset=iso-8859-1", b"caf\xe9", "caf\u00e9"),
            (200, "text/markdown", b"# changes", "# changes"),
            (200, "text/html", b"<script>x</script>", "<script>x</script>"),
        ]
        for status, content_type, data, expected in cases:
            with self.subTest(content_type=content_type):
                loader = SourceDocumentLoader(
                    SourceFetchSettings(_env_file=None),
                    httpx.MockTransport(
                        lambda request, status=status, content_type=content_type, data=data: (
                            httpx.Response(
                                status,
                                headers={"content-type": content_type},
                                stream=Body(data),
                            )
                        )
                    ),
                )
                try:
                    result = await loader.fetch("https://example.com/")
                finally:
                    await loader.aclose()
                self.assertEqual(result.text, expected)
                self.assertEqual(result.status, "fetched")
        for code in (404, 500):
            loader = SourceDocumentLoader(
                SourceFetchSettings(_env_file=None),
                httpx.MockTransport(lambda request, code=code: httpx.Response(code)),
            )
            try:
                result = await loader.fetch("https://example.com/")
            finally:
                await loader.aclose()
            self.assertEqual((result.reason, result.status_code), ("http_error", code))

    async def test_slow_dns_counts_toward_source_deadline(self):
        async def slow_resolver(host, port):
            await asyncio.sleep(60)
            return ["8.8.8.8"]

        backend = AsyncMock()
        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None, SOURCE_FETCH_SOURCE_DEADLINE=0.01),
            SourceDocumentTransport(CheckedNetworkBackend(backend, slow_resolver)),
        )
        try:
            result = await loader.fetch("http://example.com/")
        finally:
            await loader.aclose()
        self.assertEqual(result.reason, "timeout")
        backend.connect_tcp.assert_not_awaited()

    async def test_declared_size_rejected_before_reading(self):
        stream = Body(b"x")
        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None, SOURCE_FETCH_MAX_RESPONSE_BYTES=4),
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "text/plain", "content-length": "500"},
                    stream=stream,
                )
            ),
        )
        try:
            result = await loader.fetch("https://example.com/")
        finally:
            await loader.aclose()
        self.assertEqual(result.reason, "size_limit")
        self.assertTrue(stream.closed)

    async def test_redirect_limit_and_transport_failure_do_not_retry(self):
        calls = []

        def redirect(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": "/another"})

        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None, SOURCE_FETCH_MAX_REDIRECTS=0),
            httpx.MockTransport(redirect),
        )
        try:
            self.assertEqual(
                (await loader.fetch("https://example.com/start")).reason,
                "redirect_limit",
            )
        finally:
            await loader.aclose()
        self.assertEqual(len(calls), 1)

        def fail(request):
            calls.append(request)
            raise httpx.ConnectError("unavailable", request=request)

        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None), httpx.MockTransport(fail)
        )
        try:
            self.assertEqual(
                (await loader.fetch("https://example.com/start")).reason,
                "transport_error",
            )
        finally:
            await loader.aclose()
        self.assertEqual(len(calls), 2)

    async def test_redirect_to_private_host_is_blocked(self):
        stream = httpcore.AsyncMockStream(
            [
                b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/\r\nContent-Length: 0\r\n\r\n"
            ]
        )
        backend = AsyncMock()
        backend.connect_tcp.return_value = stream
        resolver = AsyncMock(return_value=["8.8.8.8"])
        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None),
            SourceDocumentTransport(CheckedNetworkBackend(backend, resolver)),
        )
        try:
            result = await loader.fetch("http://example.com/")
        finally:
            await loader.aclose()
        self.assertEqual(result.reason, "blocked_destination")
        self.assertEqual(backend.connect_tcp.await_count, 1)

    async def test_redirect_failures_and_relative_location(self):
        for location, expected in (
            (None, "invalid_redirect"),
            ("http://example.com/", "invalid_redirect"),
            ("/start", "redirect_limit"),
            ("https://user:pass@example.com/", "invalid_redirect"),
        ):
            with self.subTest(location=location):
                headers = {"location": location} if location else {}
                loader = SourceDocumentLoader(
                    SourceFetchSettings(_env_file=None),
                    httpx.MockTransport(
                        lambda request, headers=headers: httpx.Response(
                            302, headers=headers
                        )
                    ),
                )
                try:
                    result = await loader.fetch("https://example.com/start")
                finally:
                    await loader.aclose()
                self.assertEqual(result.reason, expected)

        calls = []

        def handler(request):
            calls.append(str(request.url))
            if len(calls) == 1:
                return httpx.Response(302, headers={"location": "/next"})
            return httpx.Response(
                200, headers={"content-type": "text/plain"}, stream=Body(b"ok")
            )

        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None), httpx.MockTransport(handler)
        )
        try:
            result = await loader.fetch("https://example.com/start")
        finally:
            await loader.aclose()
        self.assertEqual(result.text, "ok")
        self.assertEqual(
            calls, ["https://example.com/start", "https://example.com/next"]
        )

    async def test_deadline_and_external_cancellation_close_response(self):
        class SlowBody(Body):
            async def __aiter__(self):
                await asyncio.sleep(60)
                yield self.data

        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                stream = SlowBody(b"later")
                loader = SourceDocumentLoader(
                    SourceFetchSettings(
                        _env_file=None, SOURCE_FETCH_SOURCE_DEADLINE=0.01
                    ),
                    httpx.MockTransport(
                        lambda request, stream=stream: httpx.Response(
                            200, headers={"content-type": "text/plain"}, stream=stream
                        )
                    ),
                )
                try:
                    task = asyncio.create_task(loader.fetch("https://example.com/"))
                    if cancel:
                        await asyncio.sleep(0.001)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        self.assertEqual((await task).reason, "timeout")
                    self.assertTrue(stream.closed)
                finally:
                    await loader.aclose()

    async def test_redirect_and_anonymous_requests(self):
        seen = []

        def handler(request):
            seen.append(request)
            if len(seen) == 1:
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://other.example/final",
                        "set-cookie": "secret=x",
                    },
                )
            return httpx.Response(
                200, headers={"content-type": "text/markdown"}, stream=Body(b"Changes")
            )

        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None), httpx.MockTransport(handler)
        )
        try:
            result = await loader.fetch("https://example.com/start")
        finally:
            await loader.aclose()
        self.assertEqual(result.status, "fetched")
        self.assertEqual(result.text, "Changes")
        self.assertEqual(result.final_url, "https://other.example/final")
        self.assertEqual(result.redirect_chain, ("https://example.com/start",))
        self.assertNotIn("cookie", seen[1].headers)
        self.assertNotIn("authorization", seen[1].headers)

    async def test_content_and_http_errors(self):
        cases = [
            (200, {"content-type": "text/plain"}, b" ", "empty_content"),
            (
                200,
                {"content-type": "application/pdf"},
                b"pdf",
                "unsupported_content_type",
            ),
            (
                200,
                {"content-type": "text/plain", "content-encoding": "gzip"},
                b"x",
                "unsupported_content_encoding",
            ),
            (
                200,
                {"content-type": "text/plain; charset=no-such-charset"},
                b"x",
                "unsupported_encoding",
            ),
            (200, {"content-type": "text/plain"}, b"\xff", "decode_error"),
            (429, {"retry-after": "10"}, b"", "http_error"),
        ]
        for status, headers, body, expected in cases:
            with self.subTest(reason=expected):
                stream = Body(body)
                loader = SourceDocumentLoader(
                    SourceFetchSettings(_env_file=None),
                    httpx.MockTransport(
                        lambda request, status=status, headers=headers, stream=stream: (
                            httpx.Response(status, headers=headers, stream=stream)
                        )
                    ),
                )
                try:
                    result = await loader.fetch("https://example.com/")
                finally:
                    await loader.aclose()
                self.assertEqual(result.reason, expected)
                self.assertIsNone(result.text)
                self.assertTrue(stream.closed)
                if status == 429:
                    self.assertEqual(result.retry_after, "10")

    async def test_size_limit(self):
        stream = Body(b"12345")
        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None, SOURCE_FETCH_MAX_RESPONSE_BYTES=4),
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/plain"}, stream=stream
                )
            ),
        )
        try:
            result = await loader.fetch("https://example.com/")
        finally:
            await loader.aclose()
        self.assertEqual(result.reason, "size_limit")
        self.assertTrue(stream.closed)
