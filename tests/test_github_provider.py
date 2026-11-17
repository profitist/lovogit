import asyncio
import io
import logging
import stat
import tempfile
import threading
import unittest
import warnings
from datetime import UTC
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

import httpx

from lovogit.app.providers.github.archive import extract_archive
from lovogit.app.providers.github.client import GitHubProvider
from lovogit.app.providers.github.dto import ResolvedRepoDTO
from lovogit.app.providers.github.errors import (
    AuthenticationError,
    InvalidArchive,
    InvalidRepoURL,
    InvalidSourceResponse,
    RateLimited,
    RepoNotFound,
    RevisionUnavailable,
    SourceStorageError,
    SourceUnavailable,
)
from lovogit.app.services.repo import RepoService
from lovogit.cfg.github import GitHubSettings

SHA = "a" * 40
REPO = ResolvedRepoDTO(
    12, "Team/Repo", "https://github.com/Team/Repo", "release/main", SHA
)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self._requests = []
        self._settings = GitHubSettings(
            _env_file=None,
            GITHUB_TOKEN="test-secret",
            GITHUB_WORKSPACE_DIR=self._temp.name,
        )
        self._handler = self._default_handler
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(self._dispatch),
            headers={"Cookie": "secret-cookie", "Authorization": "default-secret"},
            auth=("user", "password"),
            follow_redirects=True,
        )
        self.addAsyncCleanup(self._client.aclose)
        self._provider = GitHubProvider(self._client, self._settings)

    async def test_resolve_and_snapshot_pins_sha_and_credentials(self):
        repo = await self._provider.resolve("https://github.com/team/repo.git/")
        self.assertEqual(repo, REPO)
        self.assertIn("release%2Fmain", self._requests[1].url.raw_path.decode())
        async with self._provider.snapshot(repo) as snapshot:
            self.assertEqual((snapshot.root / "app.py").read_text(), "print(1)")
            root = snapshot.root
            self.assertEqual(snapshot.repo.commit_sha, SHA)
            self.assertNotIn(".git", [p.name for p in root.iterdir()])
        self.assertFalse(root.exists())
        self.assertEqual(len(self._requests), 4)
        self.assertIn(SHA, str(self._requests[2].url))
        for request in self._requests:
            self.assertEqual(request.headers.get("User-Agent"), "LovoGit")
            self.assertNotIn("cookie", request.headers)
            if request.url.host == "api.github.com":
                self.assertEqual(request.headers["Authorization"], "Bearer test-secret")
            else:
                self.assertNotIn("authorization", request.headers)
        self.assertFalse(self._client.is_closed)
        self._assert_clean()

    async def test_documented_service_path_without_database(self):
        service = RepoService(self._provider)
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.execute",
            side_effect=AssertionError("Database must not be used"),
        ):
            async with service.open_source(REPO.repo_url) as snapshot:
                root = snapshot.root
                self.assertEqual((root / "app.py").read_text(), "print(1)")
        self.assertFalse(root.exists())
        self._assert_clean()

    async def test_sensitive_network_logs_are_scoped(self):
        def handler(request):
            if request.url.host == "api.github.com":
                return httpx.Response(
                    302,
                    headers={
                        "Location": "https://codeload.github.com/a/b?secret=signed-secret"
                    },
                )
            logging.getLogger("httpcore.http11").debug("signed-secret test-secret")
            return httpx.Response(200, content=zip_bytes())

        self._handler = handler
        with self.assertLogs(level="DEBUG") as logs:
            async with self._provider.snapshot(REPO):
                pass
            logging.getLogger("httpx").info("unrelated traffic")
        output = "\n".join(logs.output)
        self.assertNotIn("signed-secret", output)
        self.assertNotIn("test-secret", output)
        self.assertIn("unrelated traffic", output)

    async def test_invalid_url_no_network(self):
        with self.assertRaises(InvalidRepoURL):
            await self._provider.resolve("https://other.com/a/b")
        self.assertEqual(self._requests, [])

    async def test_status_errors_and_no_retries(self):
        for status, headers, expected in [
            (401, {}, AuthenticationError),
            (403, {}, RepoNotFound),
            (404, {}, RepoNotFound),
            (429, {"Retry-After": "5"}, RateLimited),
            (
                403,
                {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "2000000000"},
                RateLimited,
            ),
            (503, {}, SourceUnavailable),
            (422, {}, InvalidSourceResponse),
        ]:
            with self.subTest(status=status, headers=headers):
                self._requests.clear()
                self._handler = lambda r, status=status, headers=headers: (
                    httpx.Response(status, headers=headers, text="test-secret")
                )
                with self.assertRaises(expected) as error:
                    await self._provider.resolve(REPO.repo_url)
                self.assertEqual(error.exception.stage, "metadata")
                self.assertNotIn("test-secret", str(error.exception))
                self.assertEqual(len(self._requests), 1)
                if expected is RateLimited:
                    self.assertIsNotNone(error.exception.retry_at)
                    self.assertEqual(error.exception.retry_at.tzinfo, UTC)

    async def test_revision_and_invalid_json(self):
        for payload, expected in [
            (
                {"id": 1, "full_name": "a/b", "default_branch": None},
                RevisionUnavailable,
            ),
            ([], InvalidSourceResponse),
            ({}, InvalidSourceResponse),
        ]:
            self._handler = lambda r, payload=payload: httpx.Response(200, json=payload)
            with self.assertRaises(expected):
                await self._provider.resolve(REPO.repo_url)
        self._handler = lambda r: httpx.Response(200, content=b"not-json")
        with self.assertRaises(InvalidSourceResponse):
            await self._provider.resolve(REPO.repo_url)
        for status in (404, 409):
            self._handler = lambda r, status=status: (
                httpx.Response(status)
                if "/commits/" in str(r.url)
                else self._default_handler(r)
            )
            with self.assertRaises(RevisionUnavailable):
                await self._provider.resolve(REPO.repo_url)

    async def test_network_errors(self):
        for error in (httpx.ConnectError, httpx.ReadTimeout):

            def handler(request, error=error):
                raise error("sensitive-url test-secret", request=request)

            self._handler = handler
            with self.assertRaises(SourceUnavailable) as caught:
                await self._provider.resolve(REPO.repo_url)
            self.assertNotIn("test-secret", str(caught.exception))

    async def test_redirect_rejection_and_loop(self):
        for target in (
            "https://evil.com/?token=test-secret",
            "http://codeload.github.com/a",
            "https://user@codeload.github.com/a",
            "https://codeload.github.com:444/a",
            "https://api.github.com/loop",
        ):
            self._requests.clear()
            self._handler = lambda r, target=target: httpx.Response(
                302, headers={"Location": target}
            )
            with self.assertRaises(InvalidSourceResponse) as caught:
                async with self._provider.snapshot(REPO):
                    self.fail("Partial snapshot")
            self.assertNotIn(target, str(caught.exception))
            self.assertLessEqual(len(self._requests), 6)
            self._assert_clean()

    async def test_concurrent_snapshots_and_consumer_failure(self):
        async with self._provider.snapshot(REPO) as one:
            with self.assertRaisesRegex(RuntimeError, "consumer"):
                async with self._provider.snapshot(REPO) as two:
                    self.assertNotEqual(one.root, two.root)
                    raise RuntimeError("consumer")
            self.assertTrue(one.root.exists())
            self.assertFalse(two.root.exists())
        self._assert_clean()

    async def test_download_limit_and_corrupt_zip(self):
        for payload, limit in [(zip_bytes(), 2), (b"broken", 1000)]:
            self._settings.GITHUB_MAX_ARCHIVE_BYTES = limit
            self._handler = lambda r, payload=payload: httpx.Response(
                200, content=payload
            )
            with self.assertRaises(InvalidArchive):
                async with self._provider.snapshot(REPO):
                    self.fail("Partial snapshot")
            self._assert_clean()

    async def test_deadline_and_download_cancellation(self):
        started = asyncio.Event()

        async def slow(request):
            started.set()
            await asyncio.sleep(10)
            return httpx.Response(200)

        self._handler = slow
        self._settings.GITHUB_DEADLINE = 0.01
        with self.assertRaises(SourceUnavailable):
            async with self._provider.snapshot(REPO):
                self.fail("Partial snapshot")
        self._assert_clean()
        self._settings.GITHUB_DEADLINE = 120

        async def use_snapshot():
            async with self._provider.snapshot(REPO):
                self.fail("Partial snapshot")

        started.clear()
        task = asyncio.create_task(use_snapshot())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self._assert_clean()

    async def test_worker_joined_before_cancel_cleanup(self):
        started = threading.Event()
        finish = threading.Event()

        def extraction(archive, root, *limits):
            started.set()
            finish.wait(3)
            (root / "late.py").write_text("done")

        async def use_snapshot():
            async with self._provider.snapshot(REPO):
                self.fail("Cancelled snapshot")

        with patch("lovogit.app.providers.github.client.extract_archive", extraction):
            task = asyncio.create_task(use_snapshot())
            await asyncio.to_thread(started.wait, 2)
            self.assertTrue(started.is_set())
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            task.cancel()
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self._assert_clean()

    async def test_local_storage_failure(self):
        with (
            patch.object(Path, "open", side_effect=OSError("disk full")),
            self.assertRaises(SourceStorageError),
        ):
            async with self._provider.snapshot(REPO):
                self.fail("Partial snapshot")
        self._assert_clean()

    async def test_interrupted_stream(self):
        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"partial"
                raise httpx.ReadError("test-secret signed-url")

        self._handler = lambda r: httpx.Response(200, stream=BrokenStream())
        with self.assertRaises(SourceUnavailable):
            async with self._provider.snapshot(REPO):
                self.fail("Partial snapshot")
        self._assert_clean()

    async def _dispatch(self, request):
        self._requests.append(request)
        result = self._handler(request)
        return await result if asyncio.iscoroutine(result) else result

    def _default_handler(self, request):
        if "/commits/" in str(request.url):
            return httpx.Response(200, json={"sha": SHA})
        if "/zipball/" in str(request.url):
            return httpx.Response(
                302,
                headers={
                    "Location": "https://codeload.github.com/Team/Repo/zip/" + SHA
                },
            )
        if request.url.host == "codeload.github.com":
            return httpx.Response(200, content=zip_bytes())
        return httpx.Response(
            200,
            json={"id": 12, "full_name": "Team/Repo", "default_branch": "release/main"},
        )

    def _assert_clean(self):
        self.assertEqual(list(Path(self._temp.name).iterdir()), [])


class ArchiveTests(unittest.TestCase):
    def test_reject_archives_and_limits(self):
        link = ZipInfo("prefix/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        fifo = ZipInfo("prefix/fifo")
        fifo.create_system = 3
        fifo.external_attr = (stat.S_IFIFO | 0o600) << 16
        fixtures = [
            [("prefix/../../escaped", "x")],
            [("/absolute", "x")],
            [("prefix/a\\b", "x")],
            [(link, "outside")],
            [(fifo, "")],
            [("prefix/a", "x"), ("prefix/a/b", "y")],
            [("prefix/a", "x"), ("other/b", "y")],
            [("prefix/a", "x"), ("prefix/a", "y")],
            [("prefix/a/b", "x"), ("prefix/a", "y")],
        ]
        for entries in fixtures:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                archive = base / "source.zip"
                archive.write_bytes(zip_bytes(entries))
                root = base / "root"
                root.mkdir()
                with self.assertRaises(InvalidArchive):
                    extract_archive(archive, root, 1000, 100)
                self.assertFalse((base / "escaped").exists())
        for max_bytes, max_entries in [(1, 100), (1000, 1)]:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                archive = base / "source.zip"
                archive.write_bytes(
                    zip_bytes([("prefix/a", "long"), ("prefix/b", "long")])
                )
                root = base / "root"
                root.mkdir()
                with self.assertRaises(InvalidArchive):
                    extract_archive(archive, root, max_bytes, max_entries)


def zip_bytes(entries=None):
    buffer = io.BytesIO()
    with warnings.catch_warnings(), ZipFile(buffer, "w") as archive:
        warnings.filterwarnings(
            "ignore", message="Duplicate name:", category=UserWarning
        )
        for name, content in entries or [("prefix/app.py", "print(1)")]:
            archive.writestr(name, content)
    return buffer.getvalue()
