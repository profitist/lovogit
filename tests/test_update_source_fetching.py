import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpcore
import httpx
from test_pypi_provider import metadata
from test_source_document import Body
from test_update_sources import release

from lovogit.app.providers.pypi import PyPiProvider
from lovogit.app.providers.source_document import SourceDocumentLoader
from lovogit.app.providers.source_document_transport import (
    CheckedNetworkBackend,
    SourceDocumentTransport,
)
from lovogit.app.update_sources.collection import collect_sources
from lovogit.app.update_sources.dto import FetchedSourceDTO, UpdateSourceRequest
from lovogit.app.update_sources.fetching import fetch_sources
from lovogit.cfg.pypi import PyPiSettings
from lovogit.cfg.source_fetch import SourceFetchSettings


class UpdateSourceFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_source_does_not_prevent_next_source(self):
        cases = (
            ("http://[v1.foo]/", "invalid_url"),
            ("https://broken.example/", "transport_error"),
            ("https://large.example/", "size_limit"),
        )
        for url, reason in cases:
            with self.subTest(reason=reason):
                provider = AsyncMock()
                provider.get_package_info.side_effect = [
                    release(project_urls={"Changelog": url}),
                    release("2.0", project_urls={"Changelog": "https://good.example/"}),
                ]
                sources = await collect_sources(
                    UpdateSourceRequest("example", "1.0", "2.0"), provider=provider
                )

                def handler(request):
                    if request.url.host == "broken.example":
                        raise httpcore.RemoteProtocolError("malformed response")
                    if request.url.host == "large.example":
                        return httpx.Response(
                            200,
                            headers={
                                "content-type": "text/plain",
                                "content-length": "9" * 5000,
                            },
                            stream=Body(b"data"),
                        )
                    return httpx.Response(
                        200,
                        headers={"content-type": "text/plain"},
                        stream=Body(b"changes"),
                    )

                loader = SourceDocumentLoader(
                    SourceFetchSettings(_env_file=None), httpx.MockTransport(handler)
                )
                result = await fetch_sources(sources, loader=loader)
                self.assertEqual(result.results[0].reason, reason)
                self.assertEqual(result.results[0].status, "failed")
                self.assertEqual(result.results[1].status, "fetched")
                self.assertEqual(result.results[1].text, "changes")

    async def test_documented_example_with_fixture(self):
        doc = Path("docs/pypi.md").read_text()
        start = doc.index(
            "```python\nfrom lovogit.app.providers.source_document import SourceDocumentLoader"
        ) + len("```python\n")
        code = doc[start : doc.index("```", start)]
        namespace = {}
        exec(code, namespace)  # noqa: S102
        provider = AsyncMock()
        provider.get_package_info.side_effect = [release(), release("2.0")]
        loader = AsyncMock()
        result = await namespace["fetch_update_materials"](provider, loader)
        self.assertFalse(result.has_candidates)
        loader.fetch.assert_not_awaited()
        loader.aclose.assert_awaited_once()

    async def test_collect_then_fetch_with_checked_backend(self):
        requests = []

        def pypi_handler(request):
            requests.append(request.url.path)
            payload = metadata()
            payload["info"]["version"] = request.url.path.split("/")[-2]
            payload["info"]["project_urls"] = {
                "Changelog": "http://example.org/changes"
            }
            return httpx.Response(200, json=payload)

        streams = [
            httpcore.AsyncMockStream(
                [b"HTTP/1.1 302 Found\r\nLocation: /final\r\nContent-Length: 0\r\n\r\n"]
            ),
            httpcore.AsyncMockStream(
                [
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 7\r\n\r\nchanges"
                ]
            ),
        ]
        backend = AsyncMock()
        backend.connect_tcp.side_effect = streams
        resolver = AsyncMock(return_value=["8.8.8.8"])
        transport = SourceDocumentTransport(CheckedNetworkBackend(backend, resolver))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(pypi_handler)
        ) as client:
            provider = PyPiProvider(client, PyPiSettings(_env_file=None))
            sources = await collect_sources(
                UpdateSourceRequest("Example.Pkg", "1.0", "2.0"), provider=provider
            )
            backend.connect_tcp.assert_not_awaited()
            result = await fetch_sources(
                sources,
                loader=SourceDocumentLoader(
                    SourceFetchSettings(_env_file=None), transport
                ),
            )
        self.assertEqual(
            requests, ["/pypi/example-pkg/1.0/json", "/pypi/example-pkg/2.0/json"]
        )
        self.assertEqual(result.results[0].text, "changes")
        self.assertEqual(result.results[0].final_url, "http://example.org/final")
        self.assertEqual(
            [link.origin.role for link in result.results[0].links],
            ["current", "target"],
        )
        self.assertEqual(backend.connect_tcp.await_count, 2)

    async def test_limits_partial_results_and_cancellation(self):
        provider = AsyncMock()
        provider.get_package_info.side_effect = [
            release(project_urls={"Changelog": "https://one.example/"}),
            release("2.0", project_urls={"Release Notes": "https://two.example/"}),
        ]
        sources = await collect_sources(
            UpdateSourceRequest("example", "1.0", "2.0"), provider=provider
        )
        loader = AsyncMock()
        loader.fetch.side_effect = [
            FetchedSourceDTO((), "failed", "http_error"),
            FetchedSourceDTO((), "fetched", text="changes"),
        ]
        result = await fetch_sources(sources, loader=loader)
        self.assertEqual(
            [item.status for item in result.results], ["failed", "fetched"]
        )
        loader = AsyncMock()
        loader.fetch.return_value = FetchedSourceDTO((), "failed", "http_error")
        result = await fetch_sources(sources, loader=loader)
        self.assertEqual([item.status for item in result.results], ["failed", "failed"])
        loader = AsyncMock()
        loader.fetch.return_value = FetchedSourceDTO(
            (), "failed", "http_error", status_code=429
        )
        result = await fetch_sources(
            sources,
            loader=loader,
            settings=SourceFetchSettings(_env_file=None, SOURCE_FETCH_MAX_SOURCES=1),
        )
        self.assertEqual(
            [item.reason for item in result.results], ["http_error", "source_limit"]
        )
        loader.aclose.assert_awaited_once()

        async def slow(url):
            await asyncio.sleep(60)

        loader = AsyncMock()
        loader.fetch.side_effect = slow
        result = await fetch_sources(
            sources,
            loader=loader,
            settings=SourceFetchSettings(_env_file=None, SOURCE_FETCH_DEADLINE=0.01),
        )
        self.assertEqual(
            [item.reason for item in result.results], ["timeout", "deadline_exceeded"]
        )
        loader.aclose.assert_awaited_once()
        loader = AsyncMock()
        loader.fetch.side_effect = slow
        task = asyncio.create_task(fetch_sources(sources, loader=loader))
        await asyncio.sleep(0.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        loader.aclose.assert_awaited_once()

    async def test_groups_fragments_preserving_origins_and_distinct_queries(self):
        provider = AsyncMock()
        provider.get_package_info.side_effect = [
            release(project_urls={"Changelog": "https://example.org/changes#old"}),
            release(
                "2.0",
                project_urls={
                    "Changelog": "https://example.org/changes#new",
                    "Release Notes": "https://example.org/changes?v=2",
                },
            ),
        ]
        sources = await collect_sources(
            UpdateSourceRequest("example", "1.0", "2.0"), provider=provider
        )
        loader = AsyncMock()
        loader.fetch.return_value = FetchedSourceDTO(
            links=(), status="fetched", final_url="https://example.org/final", text="ok"
        )
        result = await fetch_sources(sources, loader=loader)
        self.assertTrue(result.has_candidates)
        self.assertEqual(loader.fetch.await_count, 2)
        self.assertEqual(
            loader.fetch.await_args_list[0].args, ("https://example.org/changes",)
        )
        self.assertEqual(
            loader.fetch.await_args_list[1].args, ("https://example.org/changes?v=2",)
        )
        self.assertEqual(
            result.results[0].links, (sources.current.links[0], sources.target.links[0])
        )
        self.assertEqual(result.results[1].links, (sources.target.links[1],))
        self.assertEqual(result.results[0].final_url, result.results[1].final_url)

    async def test_skips_other_and_excluded_links_without_loading(self):
        provider = AsyncMock()
        provider.get_package_info.side_effect = [
            release(project_urls={"Documentation": "https://example.org/docs"}),
            release("2.0", project_urls={"Changelog": "/relative"}),
        ]
        sources = await collect_sources(
            UpdateSourceRequest("example", "1.0", "2.0"), provider=provider
        )
        loader = AsyncMock()
        result = await fetch_sources(sources, loader=loader)
        self.assertIs(result.sources, sources)
        self.assertFalse(result.has_candidates)
        self.assertEqual([item.status for item in result.results], ["skipped"] * 2)
        self.assertEqual(
            [item.reason for item in result.results], ["not_change_link", "invalid_url"]
        )
        self.assertEqual(result.results[0].links, sources.current.links)
        self.assertEqual(result.results[1].links, sources.target.links)
        loader.assert_not_awaited()

    async def test_no_links_has_no_candidates(self):
        provider = AsyncMock()
        provider.get_package_info.side_effect = [release(), release("2.0")]
        sources = await collect_sources(
            UpdateSourceRequest("example", "1.0", "2.0"), provider=provider
        )
        loader = AsyncMock()
        result = await fetch_sources(sources, loader=loader)
        self.assertIs(result.sources, sources)
        self.assertFalse(result.has_candidates)
        self.assertEqual(result.results, ())
        loader.assert_not_awaited()
