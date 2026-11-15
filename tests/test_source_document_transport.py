import unittest
from unittest.mock import AsyncMock, patch

import httpcore
import httpx

from lovogit.app.providers.source_document import _valid_url
from lovogit.app.providers.source_document_transport import (
    BlockedDestination,
    CheckedNetworkBackend,
    SourceDocumentTransport,
)


class CheckedNetworkBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_url_and_dns_failures(self):
        for url in (
            "https://user:pass@example.com",
            "https://example.com:8443",
            "http://example.com:443",
            "https://example.com/ bad",
            "http://[broken",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                self.assertFalse(_valid_url(url))
        backend = AsyncMock()
        checked = CheckedNetworkBackend(backend, AsyncMock(side_effect=OSError("dns")))
        with self.assertRaises(OSError):
            await checked.connect_tcp("example.com", 443)
        with self.assertRaises(BlockedDestination):
            await checked.connect_tcp("sub.localhost", 443)
        backend.connect_tcp.assert_not_awaited()

    async def test_dns_changes_are_checked_on_new_connections(self):
        backend = AsyncMock()
        resolver = AsyncMock(side_effect=[["8.8.8.8"], ["127.0.0.1"]])
        checked = CheckedNetworkBackend(backend, resolver)
        await checked.connect_tcp("example.com", 443)
        with self.assertRaises(BlockedDestination):
            await checked.connect_tcp("example.com", 443)
        self.assertEqual(backend.connect_tcp.await_count, 1)

    async def test_transport_preserves_host_and_tls_name(self):
        stream = httpcore.AsyncMockStream(
            [b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"]
        )
        stream.start_tls = AsyncMock(return_value=stream)
        stream.write = AsyncMock(wraps=stream.write)
        backend = AsyncMock()
        backend.connect_tcp.return_value = stream
        resolver = AsyncMock(return_value=["8.8.8.8"])
        transport = SourceDocumentTransport(CheckedNetworkBackend(backend, resolver))
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://127.0.0.1:9999"}):
            async with httpx.AsyncClient(
                transport=transport, trust_env=False
            ) as client:
                response = await client.get("https://example.com/changes")
        self.assertEqual(response.text, "OK")
        self.assertEqual(backend.connect_tcp.await_args.args[:2], ("8.8.8.8", 443))
        self.assertEqual(
            stream.start_tls.await_args.kwargs["server_hostname"], "example.com"
        )
        self.assertIn(b"Host: example.com", stream.write.await_args_list[0].args[0])
        self.assertNotIn(b"Authorization:", stream.write.await_args_list[0].args[0])

    async def test_connects_to_checked_address(self):
        backend = AsyncMock()
        resolver = AsyncMock(return_value=["8.8.8.8"])
        checked = CheckedNetworkBackend(backend, resolver)
        await checked.connect_tcp("example.com", 443, timeout=3)
        resolver.assert_awaited_once_with("example.com", 443)
        backend.connect_tcp.assert_awaited_once_with("8.8.8.8", 443, 3, None, None)

    async def test_rejects_local_or_mixed_addresses_before_connect(self):
        for addresses in (
            ["127.0.0.1"],
            ["169.254.169.254"],
            ["8.8.8.8", "10.0.0.1"],
            ["::ffff:127.0.0.1"],
            ["224.0.0.1"],
            ["240.0.0.1"],
            ["::"],
        ):
            with self.subTest(addresses=addresses):
                backend = AsyncMock()
                checked = CheckedNetworkBackend(
                    backend, AsyncMock(return_value=addresses)
                )
                with self.assertRaises(BlockedDestination):
                    await checked.connect_tcp("example.com", 443)
                backend.connect_tcp.assert_not_awaited()
