import asyncio
import unittest
from dataclasses import asdict
from unittest.mock import patch

import httpx
from test_update_selection import dependency, metadata

from lovogit.app.providers.pypi.client import PyPiProvider
from lovogit.app.providers.pypi.errors import (
    InvalidPyPiResponse,
    PackageNotFound,
    PyPiRateLimited,
    PyPiUnavailable,
)
from lovogit.app.update_selection import (
    UpdateSelectionError,
    select_latest_stable_update,
)
from lovogit.cfg.pypi import PyPiSettings


class SelectionHTTPFixtures(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.versions = ("2.0", "3.0", "1.0")
        self.yanked = set()
        self.handler = self.default_response
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.dispatch))
        self.addAsyncCleanup(self.client.aclose)
        self.provider = PyPiProvider(self.client, PyPiSettings(_env_file=None))

    async def dispatch(self, request):
        self.requests.append(request)
        response = self.handler(request)
        return await response if asyncio.iscoroutine(response) else response

    def default_response(self, request):
        if request.url.path.startswith("/simple/"):
            body = {
                "meta": {"api-version": "1.4"},
                "name": "Example_Pkg",
                "versions": list(self.versions),
                "files": [],
            }
        else:
            version = request.url.path.split("/")[-2]
            body = {"info": asdict(metadata(version, yanked=version in self.yanked))}
        return httpx.Response(
            200, json=body, headers={"content-type": request.headers["accept"]}
        )


class SelectionHTTPErrorTests(SelectionHTTPFixtures):
    async def test_http_schema_and_transport_failures_preserve_context(self):
        for stage in ("index", "metadata"):
            for failure, expected in (
                (404, PackageNotFound),
                (429, PyPiRateLimited),
                (503, PyPiUnavailable),
                ("schema", InvalidPyPiResponse),
                ("timeout", PyPiUnavailable),
            ):
                self.requests.clear()

                def fail(request, stage=stage, failure=failure):
                    current_stage = (
                        "index"
                        if request.url.path.startswith("/simple/")
                        else "metadata"
                    )
                    if current_stage != stage:
                        return self.default_response(request)
                    if failure == "timeout":
                        raise httpx.ReadTimeout("private details", request=request)
                    if failure == "schema":
                        return httpx.Response(
                            200,
                            json={"private": "details"},
                            headers={"content-type": request.headers["accept"]},
                        )
                    return httpx.Response(
                        failure, headers={"retry-after": "60"}, text="private details"
                    )

                self.handler = fail
                with (
                    self.subTest(stage=stage, failure=failure),
                    self.assertRaises(expected) as error,
                ):
                    await select_latest_stable_update(dependency(), self.provider)
                self.assertEqual(error.exception.package, "example-pkg")
                self.assertEqual(
                    error.exception.version, None if stage == "index" else "3.0"
                )
                if failure == 429:
                    self.assertEqual(error.exception.retry_after, "60")
                self.assertNotIn("private details", str(error.exception))
                self.assertEqual(len(self.requests), 1 if stage == "index" else 2)
                self.assertFalse(self.client.is_closed)

    async def test_identity_mismatch_never_falls_back(self):
        for stage, field, value in (
            ("index", "name", "other"),
            ("metadata", "name", "other"),
            ("metadata", "version", "2.0"),
            ("metadata", "version", "legacy"),
        ):
            self.requests.clear()

            def wrong_identity(request, stage=stage, field=field, value=value):
                response = self.default_response(request)
                is_index = request.url.path.startswith("/simple/")
                if (stage == "index") == is_index:
                    body = response.json()
                    (body if is_index else body["info"])[field] = value
                    return httpx.Response(
                        200,
                        json=body,
                        headers={"content-type": request.headers["accept"]},
                    )
                return response

            self.handler = wrong_identity
            with (
                self.subTest(stage=stage, field=field),
                self.assertRaises(InvalidPyPiResponse),
            ):
                await select_latest_stable_update(dependency(), self.provider)
            self.assertEqual(len(self.requests), 1 if stage == "index" else 2)


class SelectionBudgetTests(SelectionHTTPFixtures):
    async def test_release_check_limit_and_complete_exhaustion(self):
        self.yanked = {"2.0", "3.0"}
        with self.assertRaises(UpdateSelectionError) as error:
            await select_latest_stable_update(
                dependency(), self.provider, max_release_checks=1
            )
        self.assertEqual(
            (
                error.exception.package,
                error.exception.reason,
                error.exception.checked_versions,
            ),
            ("example-pkg", "release_check_limit", ("3.0",)),
        )
        self.assertEqual(len(self.requests), 2)
        self.assertFalse(self.client.is_closed)
        self.versions = ("2.0",)
        self.requests.clear()
        result = await select_latest_stable_update(
            dependency(), self.provider, max_release_checks=1
        )
        self.assertEqual(result.status, "no_update")
        self.assertEqual(result.checked_versions, ("2.0",))
        self.assertFalse(self.client.is_closed)

    async def test_overall_deadline_covers_index_and_metadata(self):
        for stage in ("index", "metadata"):

            async def slow(request, stage=stage):
                if request.url.path.startswith("/simple/") == (stage == "index"):
                    await asyncio.sleep(1)
                return self.default_response(request)

            self.handler = slow
            with (
                self.subTest(stage=stage),
                self.assertRaises(UpdateSelectionError) as error,
            ):
                await select_latest_stable_update(
                    dependency(), self.provider, timeout=0.01
                )
            self.assertEqual(error.exception.reason, "deadline_exceeded")
            self.assertFalse(self.client.is_closed)

    async def test_provider_deadline_keeps_its_error_type(self):
        async def slow(request):
            await asyncio.sleep(1)
            return self.default_response(request)

        self.handler = slow
        provider = PyPiProvider(
            self.client, PyPiSettings(_env_file=None, PYPI_DEADLINE=0.001)
        )
        with self.assertRaises(PyPiUnavailable):
            await select_latest_stable_update(dependency(), provider, timeout=1)

    async def test_cancellation_is_not_a_normal_result_and_does_not_close_client(self):
        for stage in ("index", "metadata"):
            entered = asyncio.Event()
            blocked = asyncio.Event()

            async def wait(request, stage=stage, entered=entered, blocked=blocked):
                if request.url.path.startswith("/simple/") == (stage == "index"):
                    entered.set()
                    await blocked.wait()
                return self.default_response(request)

            self.handler = wait
            task = asyncio.create_task(
                select_latest_stable_update(dependency(), self.provider)
            )
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(self.client.is_closed)

    async def test_invalid_budgets_fail_before_network(self):
        for options in (
            *({"max_release_checks": value} for value in (0, -1, True, 1.5, "2")),
            *(
                {"timeout": value}
                for value in (
                    0,
                    -1,
                    True,
                    None,
                    float("inf"),
                    float("nan"),
                    "2",
                    10**10000,
                )
            ),
        ):
            with self.assertRaises(ValueError):
                await select_latest_stable_update(
                    dependency(), self.provider, **options
                )
        self.assertEqual(self.requests, [])

    async def test_success_keeps_client_open(self):
        result = await select_latest_stable_update(dependency(), self.provider)
        self.assertEqual(result.status, "selected")
        self.assertFalse(self.client.is_closed)

    async def test_synchronous_ranking_cannot_return_after_its_deadline(self):
        import time

        from lovogit.app.update_selection import _newer_stable_versions

        def slow_ranking(versions, current):
            time.sleep(0.01)
            return _newer_stable_versions(versions, current)

        with (
            patch(
                "lovogit.app.update_selection._newer_stable_versions",
                side_effect=slow_ranking,
            ),
            self.assertRaises(UpdateSelectionError) as error,
        ):
            await select_latest_stable_update(
                dependency(), self.provider, timeout=0.001
            )
        self.assertEqual(error.exception.reason, "deadline_exceeded")
        self.assertEqual(error.exception.checked_versions, ())
        self.assertEqual(len(self.requests), 1)
