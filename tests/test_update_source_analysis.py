import asyncio
import json
import unittest
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from test_pypi_provider import metadata as pypi_metadata
from test_source_document import Body
from test_update_sources import release

from lovogit.agent.update_sources.extractor import UpdateChangeExtractor
from lovogit.app.providers.pypi.client import PyPiProvider
from lovogit.app.providers.source_documents.loader import SourceDocumentLoader
from lovogit.app.update_sources.analysis import analyze_sources
from lovogit.app.update_sources.collection import collect_sources
from lovogit.app.update_sources.dto import (
    FetchedSourceDTO,
    SourceLinkDTO,
    UpdateSourceFetchResultDTO,
    UpdateSourceRequest,
)
from lovogit.cfg.pypi import PyPiSettings
from lovogit.cfg.source_analysis import SourceAnalysisSettings
from lovogit.cfg.source_fetch import SourceFetchSettings


async def fetched_sources(*documents, current="1.0", target="2.0"):
    provider = AsyncMock()
    provider.get_package_info.side_effect = [release(current), release(target)]
    sources = await collect_sources(
        UpdateSourceRequest("Example.Pkg", current, target), provider=provider
    )
    return UpdateSourceFetchResultDTO(sources, bool(documents), tuple(documents))


def change_record(**changes):
    return {
        "category": "api",
        "description": "remove old",
        "symbol": "old",
        "release_version": "1.1",
        "evidence": [{"source_id": "source-0", "quote": "remove old"}],
        "version_evidence": {"source_id": "source-0", "quote": "1.1"},
        **changes,
    }


async def analyze_records(records, text="1.1 remove old", **settings):
    fetched = await fetched_sources(FetchedSourceDTO((), "fetched", text=text))
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(
        content=json.dumps({"changes": records, "limitations": []})
    )
    settings = SourceAnalysisSettings(_env_file=None, **settings)
    return await analyze_sources(
        fetched, extractor=UpdateChangeExtractor(model, settings), settings=settings
    )


class UpdateSourceAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_collect_fetch_analyze(self):
        doc = Path("docs/pypi.md").read_text()
        blocks = doc.split("```python\n")
        code = next(
            block.split("```", 1)[0]
            for block in blocks[1:]
            if "async def collect_fetch_analyze" in block.split("```", 1)[0]
        )
        namespace = {}
        exec(code, namespace)  # noqa: S102
        requests = []

        def pypi_handler(request):
            requests.append(request.url.path)
            payload = pypi_metadata()
            payload["info"]["name"] = "example-pkg"
            payload["info"]["version"] = request.url.path.split("/")[-2]
            return httpx.Response(200, json=payload)

        pages = []

        def document_handler(request):
            pages.append(str(request.url))
            if request.url.path == "/changes":
                return httpx.Response(302, headers={"location": "/final"})
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                stream=Body(b"1.1 remove old"),
            )

        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content=json.dumps({"changes": [change_record()], "limitations": []})
        )
        loader = SourceDocumentLoader(
            SourceFetchSettings(_env_file=None), httpx.MockTransport(document_handler)
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(pypi_handler)
        ) as client:
            result = await namespace["collect_fetch_analyze"](
                PyPiProvider(client, PyPiSettings(_env_file=None)), loader, model
            )
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            pages, ["https://example.org/changes", "https://example.org/final"]
        )
        self.assertEqual(
            result.changes[0].evidence[0].final_url, "https://example.org/final"
        )
        self.assertEqual(
            [link.origin.role for link in result.changes[0].evidence[0].links],
            ["current", "target"],
        )
        model.ainvoke.assert_awaited_once()

    async def test_all_skipped_all_failed_and_partial_fetch(self):
        settings = SourceAnalysisSettings(
            _env_file=None, SOURCE_ANALYSIS_MAX_DOCUMENT_CHARS=1
        )
        model = AsyncMock()
        result = await analyze_sources(
            await fetched_sources(FetchedSourceDTO((), "fetched", text="long")),
            extractor=UpdateChangeExtractor(model, settings),
            settings=settings,
        )
        self.assertEqual(result.status, "partial")
        model.ainvoke.assert_not_awaited()
        settings = SourceAnalysisSettings(_env_file=None)
        model.ainvoke.side_effect = RuntimeError("provider")
        result = await analyze_sources(
            await fetched_sources(FetchedSourceDTO((), "fetched", text="long")),
            extractor=UpdateChangeExtractor(model, settings),
            settings=settings,
        )
        self.assertEqual(result.status, "failed")
        model.ainvoke.side_effect = None
        model.ainvoke.return_value = AIMessage(
            content='{"changes": [], "limitations": []}'
        )
        result = await analyze_sources(
            await fetched_sources(
                FetchedSourceDTO((), "fetched", text="text"),
                FetchedSourceDTO((), "failed", "http_error"),
            ),
            extractor=UpdateChangeExtractor(model, settings),
            settings=settings,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.coverage, "undetermined")
        self.assertIn("unavailable_materials", result.limitations)

    async def test_blank_and_foreign_evidence_are_rejected(self):
        for evidence in (
            [{"source_id": "source-0", "quote": ""}],
            [{"source_id": "source-0", "quote": " "}],
            [{"source_id": "source-1", "quote": "remove old"}],
        ):
            with self.subTest(evidence=evidence):
                result = await analyze_records([change_record(evidence=evidence)])
                self.assertEqual(len(result.rejected), 1)
                self.assertEqual(result.changes, ())

    async def test_conflicting_documents_preserve_origins(self):
        fetched = await fetched_sources(
            FetchedSourceDTO(
                (), "fetched", text="1.1 remove old", final_url="https://one.example"
            ),
            FetchedSourceDTO(
                (), "fetched", text="1.1 keep old", final_url="https://two.example"
            ),
        )
        model = AsyncMock()
        model.ainvoke.side_effect = [
            AIMessage(
                content=json.dumps({"changes": [change_record()], "limitations": []})
            ),
            AIMessage(
                content=json.dumps(
                    {
                        "changes": [
                            change_record(
                                description="keep old",
                                evidence=[
                                    {"source_id": "source-1", "quote": "keep old"}
                                ],
                                version_evidence={
                                    "source_id": "source-1",
                                    "quote": "1.1",
                                },
                            )
                        ],
                        "limitations": [],
                    }
                )
            ),
        ]
        settings = SourceAnalysisSettings(_env_file=None)
        result = await analyze_sources(
            fetched, extractor=UpdateChangeExtractor(model, settings), settings=settings
        )
        self.assertEqual(len(result.changes), 2)
        self.assertEqual(
            [item.evidence[0].final_url for item in result.changes],
            ["https://one.example", "https://two.example"],
        )

    async def test_documented_analysis_example(self):
        doc = Path("docs/pypi.md").read_text()
        start = doc.index(
            "```python\nfrom lovogit.agent.update_sources.extractor"
        ) + len("```python\n")
        namespace = {}
        exec(doc[start : doc.index("```", start)], namespace)  # noqa: S102
        model = AsyncMock()
        model.ainvoke.return_value = AIMessage(
            content='{"changes": [], "limitations": []}'
        )
        result = await namespace["analyze_update_materials"](
            await fetched_sources(FetchedSourceDTO((), "fetched", text="hello")), model
        )
        self.assertEqual(result.status, "completed")

    async def test_valid_and_rejected_records_are_independent(self):
        records = [
            change_record(),
            {},
            change_record(evidence=[{"source_id": "other", "quote": "remove old"}]),
            change_record(evidence=[{"source_id": "source-0", "quote": "invented"}]),
            change_record(symbol="invented"),
        ]
        result = await analyze_records(records, "1.1 remove old remove old")
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].evidence[0].start, 4)
        self.assertEqual(
            [item.reason for item in result.rejected],
            ["invalid_record", "unknown_source", "invalid_quote", "unsupported_symbol"],
        )
        self.assertEqual(result.status, "partial")

    async def test_interval_and_unknown_versions(self):
        records = [
            change_record(
                release_version=version,
                version_evidence={"source_id": "source-0", "quote": version},
            )
            for version in ("1.0", "1.1", "1.2", "2.0", "3.0")
        ]
        records.extend(
            [
                change_record(release_version=None, version_evidence=None),
                change_record(
                    release_version="legacy",
                    version_evidence={"source_id": "source-0", "quote": "legacy"},
                ),
                change_record(version_evidence=None),
            ]
        )
        result = await analyze_records(records, "1.0 1.1 1.2 2.0 3.0 legacy remove old")
        self.assertEqual(
            [item.release_version for item in result.changes], ["1.1", "1.2", "2.0"]
        )
        self.assertEqual(len(result.unscoped_changes), 3)
        self.assertEqual(
            [item.reason for item in result.rejected], ["outside_interval"] * 2
        )
        self.assertEqual(result.coverage, "undetermined")

    async def test_categories_without_symbols_and_empty_success(self):
        for category in (
            "api",
            "behavior",
            "configuration",
            "default_value",
            "python_requirement",
            "dependency_requirement",
        ):
            result = await analyze_records(
                [change_record(category=category, symbol=None)]
            )
            self.assertEqual(result.changes[0].category, category)
        result = await analyze_records([])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.coverage, "undetermined")
        self.assertEqual(result.changes, ())

    async def test_limits_and_partial_model_failures(self):
        documents = [
            FetchedSourceDTO((), "fetched", text="x" * 5),
            FetchedSourceDTO((), "fetched", text="ok"),
        ]
        for overrides, reasons in (
            ({"SOURCE_ANALYSIS_MAX_DOCUMENT_CHARS": 4}, ["document_size_limit", None]),
            ({"SOURCE_ANALYSIS_MAX_TOTAL_CHARS": 4}, ["total_size_limit", None]),
            ({"SOURCE_ANALYSIS_MAX_DOCUMENTS": 1}, [None, "document_limit"]),
        ):
            settings = SourceAnalysisSettings(_env_file=None, **overrides)
            model = AsyncMock()
            model.ainvoke.return_value = AIMessage(
                content='{"changes": [], "limitations": []}'
            )
            result = await analyze_sources(
                await fetched_sources(*documents),
                extractor=UpdateChangeExtractor(model, settings),
                settings=settings,
            )
            self.assertEqual([item.reason for item in result.documents], reasons)
            self.assertEqual(model.ainvoke.await_count, 1)
            self.assertEqual(result.status, "partial")
            model.aclose.assert_not_awaited()
        settings = SourceAnalysisSettings(_env_file=None)
        model = AsyncMock()
        model.ainvoke.side_effect = [
            AIMessage(content='{"changes": [], "limitations": []}'),
            RuntimeError("provider failed"),
        ]
        result = await analyze_sources(
            await fetched_sources(*documents),
            extractor=UpdateChangeExtractor(model, settings),
            settings=settings,
        )
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.documents[1].reason, "model_error")

    async def test_deadline_and_external_cancellation(self):

        async def slow(messages):
            await asyncio.sleep(60)

        fetched = await fetched_sources(
            FetchedSourceDTO((), "fetched", text="a"),
            FetchedSourceDTO((), "fetched", text="b"),
        )
        settings = SourceAnalysisSettings(_env_file=None, SOURCE_ANALYSIS_DEADLINE=0.01)
        model = AsyncMock()
        model.ainvoke.side_effect = slow
        analyze = partial(
            analyze_sources,
            extractor=UpdateChangeExtractor(model, settings),
            settings=settings,
        )
        result = await analyze(fetched)
        self.assertEqual(
            [item.reason for item in result.documents], ["timeout", "deadline_exceeded"]
        )
        model = AsyncMock()
        model.ainvoke.side_effect = slow
        analyze = partial(
            analyze_sources,
            extractor=UpdateChangeExtractor(model, settings),
            settings=settings,
        )
        task = asyncio.create_task(analyze(fetched))
        await asyncio.sleep(0.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        model.aclose.assert_not_awaited()

    def test_independent_settings(self):
        settings = SourceAnalysisSettings(_env_file=None)
        self.assertEqual(
            list(settings.model_dump().values()), [10, 60000, 180000, 60000, 30, 120]
        )
        for field in SourceAnalysisSettings.model_fields:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                SourceAnalysisSettings(_env_file=None, **{field: 0})

    async def test_version_intervals(self):
        for current, target in (
            ("1.9", "1.10"),
            ("1.0rc1", "1.0"),
            ("1.0", "1.0.post1"),
            ("1.0", "1.0+local"),
        ):
            with self.subTest(current=current, target=target):
                fetched = await fetched_sources(current=current, target=target)
                result = await analyze_sources(fetched, extractor=AsyncMock())
                self.assertEqual(result.status, "no_materials")
        for current, target in (("1.0", "1.0.0"), ("2", "1"), ("legacy", "2")):
            with self.subTest(current=current, target=target):
                fetched = await fetched_sources(current=current, target=target)
                extractor = AsyncMock()
                with self.assertRaises(ValueError):
                    await analyze_sources(fetched, extractor=extractor)
                extractor.extract.assert_not_awaited()

    async def test_release_identity_mismatch(self):
        fetched = await fetched_sources()
        for metadata in (
            replace(fetched.sources.target.metadata, name="other"),
            replace(fetched.sources.target.metadata, version="3.0"),
        ):
            invalid = replace(
                fetched,
                sources=replace(
                    fetched.sources,
                    target=replace(fetched.sources.target, metadata=metadata),
                ),
            )
            with self.assertRaises(ValueError):
                await analyze_sources(invalid, extractor=AsyncMock())

    async def test_unavailable_documents_do_not_call_model(self):
        fetched = await fetched_sources(
            FetchedSourceDTO((), "failed", "http_error"),
            FetchedSourceDTO((), "skipped", "source_limit"),
            FetchedSourceDTO((), "fetched", text="  "),
        )
        extractor = AsyncMock()
        links = tuple(
            SourceLinkDTO(
                release_source.origin,
                "project_urls",
                "Changelog",
                "https://example.org/",
                "changelog",
            )
            for release_source in (fetched.sources.current, fetched.sources.target)
        )
        fetched = replace(
            fetched,
            results=(replace(fetched.results[0], links=links), *fetched.results[1:]),
        )
        result = await analyze_sources(fetched, extractor=extractor)
        self.assertIs(result.fetched, fetched)
        self.assertEqual(result.status, "no_materials")
        self.assertEqual(result.fetched.results[0].links, links)
        self.assertEqual(
            [item.source_id for item in result.documents],
            ["source-0", "source-1", "source-2"],
        )
        extractor.extract.assert_not_awaited()
