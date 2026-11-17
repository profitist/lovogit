import asyncio
import unittest
from dataclasses import replace
from functools import partial
from unittest.mock import call, create_autospec

from lovogit.app.providers.pypi.client import PyPiProvider
from lovogit.app.providers.pypi.dto import PackageInfoDTO
from lovogit.app.providers.pypi.errors import (
    InvalidPyPiResponse,
    PackageNotFound,
    PyPiRateLimited,
    PyPiUnavailable,
)
from lovogit.app.update_sources.collection import collect_sources
from lovogit.app.update_sources.dto import UpdateSourceRequest


def release(version="1.0", **changes):
    return replace(
        PackageInfoDTO(
            name="Example_Pkg",
            version=version,
            summary=None,
            description=" README\n",
            description_content_type="text/markdown",
            requires_python=None,
            requires_dist=(),
            dynamic=(),
            project_urls={},
            home_page=None,
            package_url=None,
            release_url=None,
            yanked=False,
            yanked_reason=None,
        ),
        **changes,
    )


class UpdateSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.provider = create_autospec(PyPiProvider, instance=True)
        self.current = release()
        self.target = release("2.0")
        self.provider.get_package_info.side_effect = [self.current, self.target]
        self.collect = partial(collect_sources, provider=self.provider)
        self.request = UpdateSourceRequest("Example.Pkg", "1.0", "2.0")

    async def test_collects_exact_versions_and_preserves_requested_and_returned_identity(
        self,
    ):
        self.request = replace(self.request, current_version="1", target_version="2")
        result = await self.collect(self.request)
        self.assertEqual(result.request, self.request)
        self.assertEqual(result.current.origin.role, "current")
        self.assertEqual(result.target.origin.role, "target")
        self.assertEqual(result.current.origin.requested_version, "1")
        self.assertEqual(result.current.origin.version, "1.0")
        self.assertEqual(result.target.origin.requested_version, "2")
        self.assertEqual(result.target.origin.version, "2.0")
        self.assertEqual(result.current.origin.package, "Example_Pkg")
        self.assertEqual(result.current.metadata, self.current)
        self.assertEqual(result.target.metadata, self.target)
        self.assertEqual(
            self.provider.get_package_info.await_args_list,
            [call("Example.Pkg", "1"), call("Example.Pkg", "2")],
        )
        self.provider.get_package.assert_not_called()

    async def test_identical_versions_keep_both_roles_with_one_request(self):
        result = await self.collect(replace(self.request, target_version="1.0"))
        self.assertEqual(result.current.metadata, result.target.metadata)
        self.assertEqual(result.current.origin.role, "current")
        self.assertEqual(result.target.origin.role, "target")
        self.provider.get_package_info.assert_awaited_once_with("Example.Pkg", "1.0")

    async def test_descriptions_keep_raw_text_content_type_and_provenance(self):
        for text, available in [
            (None, False),
            ("", False),
            (" \n\t", False),
            (" README\n", True),
        ]:
            with self.subTest(text=text):
                self.provider.get_package_info.side_effect = [
                    replace(self.current, description=text),
                    replace(self.target, description=text),
                ]
                result = await self.collect(self.request)
                for material, role, version in [
                    (result.current, "current", "1.0"),
                    (result.target, "target", "2.0"),
                ]:
                    self.assertEqual(material.metadata.description, text)
                    self.assertEqual(
                        material.metadata.description_content_type, "text/markdown"
                    )
                    self.assertEqual(material.description_available, available)
                    self.assertEqual(material.origin.source_kind, "pypi_metadata")
                    self.assertEqual(material.origin.role, role)
                    self.assertEqual(material.origin.version, version)

    async def test_empty_required_fields_rejected_before_provider_call(self):
        for field in ("package", "current_version", "target_version"):
            for value in ("", " \t\n", None):
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaisesRegex(ValueError, field),
                ):
                    await self.collect(replace(self.request, **{field: value}))
        self.provider.get_package_info.assert_not_called()

    async def test_provider_errors_propagate_without_retry_or_partial_result(self):
        for error_type in (
            PackageNotFound,
            PyPiRateLimited,
            PyPiUnavailable,
            InvalidPyPiResponse,
        ):
            for version, calls in [("1.0", 1), ("2.0", 2)]:
                with self.subTest(error=error_type, version=version):
                    error = error_type(
                        "example-pkg",
                        version,
                        "failure",
                        status_code=429,
                        retry_after="60",
                    )
                    self.provider.get_package_info.reset_mock()
                    self.provider.get_package_info.side_effect = (
                        [error] if calls == 1 else [self.current, error]
                    )
                    with self.assertRaises(error_type) as caught:
                        await self.collect(self.request)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(caught.exception.version, version)
                    self.assertEqual(caught.exception.retry_after, "60")
                    self.assertEqual(self.provider.get_package_info.await_count, calls)

    async def test_cancellation_and_input_errors_propagate_for_both_roles(self):
        for error in (asyncio.CancelledError(), ValueError("invalid version")):
            for calls in (1, 2):
                with self.subTest(error=type(error), calls=calls):
                    self.provider.get_package_info.reset_mock()
                    self.provider.get_package_info.side_effect = (
                        [error] if calls == 1 else [self.current, error]
                    )
                    with self.assertRaises(type(error)) as caught:
                        await self.collect(self.request)
                    self.assertIs(caught.exception, error)
                    self.assertEqual(self.provider.get_package_info.await_count, calls)

    async def test_published_links_keep_labels_urls_and_role_provenance(self):
        urls = {
            "Change_Log": "https://example.org/changes?lang=en#v1",
            "CHANGES": "https://example.org/history",
            "History": "https://example.org/history",
            "Release-Notes": "https://example.org/releases",
            "Source": "https://example.org/source",
            "Source Code": "https://example.org/source",
            "Repository": "https://example.org/source",
            "Code": "https://example.org/source",
            "Documentation": "https://example.org/docs",
            "Docs": "https://example.org/docs",
            "Homepage": "https://example.org",
            "Home": "https://example.org",
            "Community": "https://github.com/example/pkg/releases",
            "Release.Notes": "https://example.org/releases",
        }
        categories = (
            ["changelog"] * 3
            + ["release_notes"]
            + ["source"] * 4
            + ["documentation"] * 2
            + ["homepage"] * 2
            + ["other"] * 2
        )
        self.provider.get_package_info.side_effect = [
            replace(
                self.current, project_urls=urls, home_page="https://example.org/home"
            ),
            replace(
                self.target,
                project_urls={"Changelog": "https://example.org/new"},
                home_page="https://example.org/home",
            ),
        ]
        result = await self.collect(self.request)
        self.assertEqual(
            [(link.label, link.url) for link in result.current.links[:-1]],
            list(urls.items()),
        )
        self.assertEqual(
            [link.category for link in result.current.links[:-1]], categories
        )
        for material in (result.current, result.target):
            for link in material.links:
                self.assertEqual(link.origin, material.origin)
            self.assertEqual(material.links[-1].field, "home_page")
            self.assertEqual(material.links[-1].category, "homepage")
        self.assertEqual(result.target.links[0].url, "https://example.org/new")

    async def test_missing_homepage_is_not_a_link_and_empty_project_url_is_preserved(
        self,
    ):
        self.provider.get_package_info.side_effect = [
            replace(self.current, home_page="", project_urls={"Changelog": ""}),
            self.target,
        ]
        result = await self.collect(self.request)
        self.assertEqual(len(result.current.links), 1)
        self.assertEqual(result.current.links[0].url, "")
        self.assertEqual(result.target.links, ())

    async def test_change_link_availability_and_exclusion_reasons(self):
        for url, reason in [
            ("", "empty_url"),
            ("/changes", "invalid_url"),
            ("not a url", "invalid_url"),
            ("https:///changes", "invalid_url"),
            ("https://[broken", "invalid_url"),
            ("https://example.org:bad", "invalid_url"),
            ("https://example.org:65536", "invalid_url"),
            ("https://example.org/with space", "invalid_url"),
            ("https://example.org/\nchanges", "invalid_url"),
            ("https://example.org/\x00changes", "invalid_url"),
            ("https://example.org/\x85changes", "invalid_url"),
            ("https://example.org\\other", "invalid_url"),
            ("https://user:pass@example.org/changes", "credentials_not_allowed"),
            ("https://@example.org/changes", "credentials_not_allowed"),
            ("ftp://example.org/changes", "unsupported_scheme"),
            ("javascript:alert(1)", "unsupported_scheme"),
            ("https://example.org/changes?lang=en#v2", None),
            ("http://example.org/changes", None),
            ("https://[::1]:443/changes", None),
        ]:
            with self.subTest(url=url):
                self.provider.get_package_info.side_effect = [
                    self.current,
                    replace(self.target, project_urls={"Changelog": url}),
                ]
                result = await self.collect(self.request)
                self.assertEqual(result.target.links[0].url, url)
                self.assertEqual(result.target.links[0].exclusion_reason, reason)
                self.assertEqual(result.target.has_change_links, reason is None)
                self.assertEqual(result.has_change_links, reason is None)
                self.assertFalse(result.current.has_change_links)

    async def test_only_explicit_change_labels_count_in_either_role(self):
        for role in ("current", "target"):
            self.provider.get_package_info.side_effect = [
                replace(
                    self.current,
                    project_urls={"Release Notes": "https://example.org/releases"},
                )
                if role == "current"
                else self.current,
                replace(
                    self.target,
                    project_urls={"Release Notes": "https://example.org/releases"},
                )
                if role == "target"
                else self.target,
            ]
            result = await self.collect(self.request)
            self.assertTrue(result.has_change_links)
            self.assertTrue(getattr(result, role).has_change_links)
        for urls in (
            {},
            {
                "Source": "https://example.org",
                "Documentation": "https://example.org/releases",
            },
        ):
            self.provider.get_package_info.side_effect = [
                self.current,
                replace(self.target, project_urls=urls),
            ]
            result = await self.collect(self.request)
            self.assertFalse(result.has_change_links)

    async def test_requirement_diff_preserves_raw_metadata_and_does_not_resolve_markers(
        self,
    ):
        before = ("zlib>=1", "lib[extra]>=1; python_version < '3.12'")
        after = ("zlib>=2", "lib[extra]>=2; python_version < '3.12'", "aaa>=1")
        self.provider.get_package_info.side_effect = [
            replace(self.current, requires_python=">=3.10", requires_dist=before),
            replace(self.target, requires_python=">=3.11", requires_dist=after),
        ]
        result = await self.collect(self.request)
        diff = result.requirements_diff
        self.assertEqual(diff.current_requires_python, ">=3.10")
        self.assertEqual(diff.target_requires_python, ">=3.11")
        self.assertTrue(diff.requires_python_changed)
        self.assertEqual(
            diff.added_requires_dist,
            ("aaa>=1", "lib[extra]>=2; python_version < '3.12'", "zlib>=2"),
        )
        self.assertEqual(
            diff.removed_requires_dist,
            ("lib[extra]>=1; python_version < '3.12'", "zlib>=1"),
        )
        self.assertEqual(result.current.metadata.requires_dist, before)
        self.assertEqual(result.target.metadata.requires_dist, after)

    async def test_requirement_order_duplicates_and_missing_python_requirement(self):
        self.provider.get_package_info.side_effect = [
            replace(self.current, requires_dist=("b", "a", "a")),
            replace(self.target, requires_dist=("a", "b")),
        ]
        result = await self.collect(self.request)
        self.assertEqual(result.requirements_diff.added_requires_dist, ())
        self.assertEqual(result.requirements_diff.removed_requires_dist, ())
        self.assertFalse(result.requirements_diff.requires_python_changed)
        self.assertEqual(result.current.metadata.requires_dist, ("b", "a", "a"))
        for before, after in [
            (None, ">=3.11"),
            (">=3.11", ">= 3.11"),
            (">=3.11", None),
        ]:
            self.provider.get_package_info.side_effect = [
                replace(self.current, requires_python=before),
                replace(self.target, requires_python=after),
            ]
            result = await self.collect(self.request)
            self.assertTrue(result.requirements_diff.requires_python_changed)

    async def test_dynamic_requirements_and_yanked_target_remain_release_facts(self):
        for role in ("current", "target"):
            for reason in (None, "", "withdrawn"):
                with self.subTest(role=role, reason=reason):
                    self.provider.get_package_info.side_effect = [
                        replace(
                            self.current,
                            dynamic=("requires_dist",) if role == "current" else (),
                        ),
                        replace(
                            self.target,
                            dynamic=("requires_dist",) if role == "target" else (),
                            yanked=True,
                            yanked_reason=reason,
                        ),
                    ]
                    result = await self.collect(self.request)
                    self.assertEqual(
                        result.current.requires_dist_dynamic, role == "current"
                    )
                    self.assertEqual(
                        result.target.requires_dist_dynamic, role == "target"
                    )
                    self.assertEqual(
                        getattr(result, role).metadata.dynamic, ("requires_dist",)
                    )
                    self.assertEqual(result.current.metadata.requires_dist, ())
                    self.assertEqual(result.target.metadata.requires_dist, ())
                    self.assertTrue(result.target.metadata.yanked)
                    self.assertEqual(result.target.metadata.yanked_reason, reason)
                    self.assertEqual(result.target.metadata.version, "2.0")


class UpdateSourceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_provider_collects_only_two_pypi_release_endpoints(self):
        import httpx

        from lovogit.cfg.pypi import PyPiSettings

        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(request.url.host, "pypi.org")
            self.assertEqual(request.method, "GET")
            self.assertIn(
                request.url.path,
                ("/pypi/example-pkg/1.0/json", "/pypi/example-pkg/2.0/json"),
            )
            version = request.url.path.split("/")[-2]
            return httpx.Response(
                200,
                json={
                    "info": {
                        "name": "example-pkg",
                        "version": version,
                        "description": "Same README",
                        "description_content_type": "text/markdown",
                        "project_urls": {
                            "Changelog": "https://external.example/changes"
                        },
                        "requires_dist": None,
                        "dynamic": ["requires_dist"],
                        "yanked": version == "2.0",
                        "yanked_reason": "" if version == "2.0" else None,
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collect = partial(
                collect_sources,
                provider=PyPiProvider(client, PyPiSettings(_env_file=None)),
            )
            result = await collect(UpdateSourceRequest("Example.Pkg", "1.0", "2.0"))
            self.assertFalse(client.is_closed)
        self.assertEqual(len(requests), 2)
        self.assertEqual(result.current.origin.package, "example-pkg")
        self.assertTrue(result.current.has_change_links)
        self.assertTrue(result.target.has_change_links)
        self.assertEqual(result.current.links[0].origin.role, "current")
        self.assertEqual(result.target.links[0].origin.role, "target")
        self.assertEqual(result.target.metadata.requires_dist, ())
        self.assertTrue(result.target.requires_dist_dynamic)
        self.assertTrue(result.target.metadata.yanked)
        self.assertEqual(result.target.metadata.yanked_reason, "")

    async def test_cold_import_and_call_without_unrelated_subsystems_or_credentials(
        self,
    ):
        import os
        import sys
        from pathlib import Path

        source = str(Path(__file__).resolve().parents[1] / "src")
        script = "import asyncio\nimport sys\nimport httpx\nfrom lovogit.app.providers.pypi.client import PyPiProvider\nfrom lovogit.app.update_sources.dto import UpdateSourceRequest\nfrom lovogit.app.update_sources.collection import collect_sources\nfrom lovogit.cfg.pypi import PyPiSettings\n\nasync def main():\n\n    def handler(request):\n        return httpx.Response(200, json={'info': {'name': 'example', 'version': '1'}})\n    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:\n        result = await collect_sources(UpdateSourceRequest('example', '1', '1'), provider=PyPiProvider(client, PyPiSettings(_env_file=None)))\n        assert result.current.origin.role == 'current'\n        assert result.target.origin.role == 'target'\n    forbidden = ('lovogit.db', 'lovogit.bot', 'lovogit.agent', 'lovogit.sandbox', 'lovogit.cfg.settings', 'langchain', 'langgraph')\n    assert not any((name == prefix or name.startswith(prefix + '.') for name in sys.modules for prefix in forbidden))\nasyncio.run(main())\n"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            env={"PYTHONPATH": source, "PATH": os.defpath},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        self.assertEqual(process.returncode, 0, (stdout + stderr).decode())
