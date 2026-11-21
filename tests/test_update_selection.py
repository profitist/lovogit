import unittest
from dataclasses import replace
from unittest.mock import create_autospec

from lovogit.app.dependencies.dto import DependencyDeclarationDTO, ProvenanceDTO
from lovogit.app.providers.pypi.client import PyPiProvider
from lovogit.app.providers.pypi.dto import PackageIndexDTO, PackageInfoDTO
from lovogit.app.update_selection import select_latest_stable_update


def dependency(**changes):
    return replace(
        DependencyDeclarationDTO(
            name="example-pkg",
            original_name="Example_Pkg",
            raw_requirement="Example_Pkg==1.0",
            specifier="==1.0",
            extras=(),
            marker=None,
            scope="runtime",
            origins=(ProvenanceDTO("requirements.txt", start_line=1, end_line=1),),
            version_state="declared_pin",
            current_version="1.0",
            reason=None,
        ),
        **changes,
    )


def metadata(version="2.0", **changes):
    return replace(
        PackageInfoDTO(
            name="example-pkg",
            version=version,
            summary=None,
            description="changes",
            description_content_type="text/plain",
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


class CurrentVersionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = create_autospec(PyPiProvider, instance=True)
        self.provider.get_package.return_value = PackageIndexDTO("example-pkg", (), ())

    async def test_skips_unusable_current_without_network(self):
        for changes, reason in (
            (
                {"current_version": None, "version_state": "unresolved"},
                "unknown_current",
            ),
            (
                {"current_version": "2", "version_state": "conflict"},
                "conflicting_current",
            ),
            ({"version_state": "unsupported"}, "unsupported_current"),
            ({"source_kind": "direct_url"}, "unsupported_source"),
            ({"source_kind": "editable"}, "unsupported_source"),
            ({"current_version": "legacy"}, "invalid_current"),
            ({"current_version": "9" * 5000}, "invalid_current"),
            ({"current_version": None}, "unknown_current"),
        ):
            with self.subTest(changes=tuple(changes)):
                record = dependency(**changes)
                result = await select_latest_stable_update(record, self.provider)
                self.assertEqual((result.status, result.reason), ("skipped", reason))
                self.assertIs(result.dependency, record)
                self.assertIsNone(result.request)
                self.assertIsNone(result.target_metadata)
        self.provider.get_package.assert_not_awaited()
        self.provider.get_package_info.assert_not_awaited()

    async def test_known_current_name_and_provenance(self):
        for state in ("declared_pin", "locked"):
            record = dependency(name="Example_Pkg", version_state=state)
            result = await select_latest_stable_update(record, self.provider)
            self.assertIs(result.dependency, record)
            self.assertEqual(result.dependency.origins[0].path, "requirements.txt")
            self.provider.get_package.assert_awaited_with("example-pkg")
        self.provider.get_package_info.assert_not_awaited()

    async def test_invalid_name_is_input_error(self):
        with self.assertRaisesRegex(ValueError, "invalid package name"):
            await select_latest_stable_update(
                dependency(name="bad/name"), self.provider
            )
        self.provider.get_package.assert_not_awaited()


class StableSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = create_autospec(PyPiProvider, instance=True)
        self.provider.get_package_info.side_effect = lambda name, version: metadata(
            version
        )

    async def test_numeric_order_and_exclusions(self):
        versions = (
            "2.9",
            "3.0rc1",
            "2.10",
            "4.0.dev1",
            "5.0+local",
            "legacy",
            "9" * 5000,
        )
        for ordered in (versions, tuple(reversed(versions))):
            self.provider.get_package.return_value = PackageIndexDTO(
                "example-pkg", ordered, ()
            )
            result = await select_latest_stable_update(
                dependency(current_version="2.0"), self.provider
            )
            self.assertEqual(result.request.target_version, "2.10")
            self.assertEqual(
                {item.reason for item in result.excluded_versions},
                {"prerelease", "dev_release", "local_version", "invalid_version"},
            )
        self.assertEqual(self.provider.get_package_info.await_count, 2)

    async def test_epoch_post_and_equivalent_versions(self):
        from itertools import permutations

        self.provider.get_package.return_value = PackageIndexDTO(
            "example-pkg", ("2.0", "1!1.0", "1!1.0.post1"), ()
        )
        result = await select_latest_stable_update(
            dependency(current_version="2.0"), self.provider
        )
        self.assertEqual(result.request.target_version, "1!1.0.post1")
        versions = ("02.0", "2.0", "v2.0", "2.00", "2.0")
        results = set()
        for ordered in permutations(versions[:3]):
            self.provider.get_package_info.reset_mock()
            self.provider.get_package.return_value = PackageIndexDTO(
                "example-pkg", ordered + versions[3:], ()
            )
            result = await select_latest_stable_update(dependency(), self.provider)
            results.add(result.request.target_version)
            self.provider.get_package_info.assert_awaited_once_with(
                "example-pkg", "2.0"
            )
        self.assertEqual(results, {"2.0"})

    async def test_no_downgrade_no_eligible_candidate_and_no_metadata_call(self):
        for current, versions in (
            ("1.0", ("1", "1.0", "1.0.0")),
            ("1.0", ()),
            ("1.0", ("2.0rc1", "2.0.dev1", "legacy")),
            ("3.0", ("1.0", "2.0")),
            ("3.0rc1", ("1.0", "2.0")),
            ("2.0+local", ("1.0", "2.0")),
        ):
            with self.subTest(current=current, versions=versions):
                self.provider.get_package.return_value = PackageIndexDTO(
                    "example-pkg", versions, ()
                )
                result = await select_latest_stable_update(
                    dependency(current_version=current), self.provider
                )
                self.assertEqual(
                    (result.status, result.reason), ("no_update", "no_newer_stable")
                )
                self.assertEqual(result.checked_versions, ())
                self.assertIsNone(result.request)
                self.assertIsNone(result.target_metadata)
        self.provider.get_package_info.assert_not_awaited()


class YankedSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = create_autospec(PyPiProvider, instance=True)
        self.provider.get_package.return_value = PackageIndexDTO(
            "example-pkg", ("2.0", "3.0", "4.0rc1"), ()
        )

    async def test_yanked_newest_then_selected_metadata_and_request(self):
        target = metadata("2.0")
        self.provider.get_package_info.side_effect = [
            metadata("3.0", yanked=True, yanked_reason="broken"),
            target,
        ]
        record = dependency(current_version="1.00")
        result = await select_latest_stable_update(record, self.provider)
        self.assertEqual(result.status, "selected")
        self.assertIs(result.target_metadata, target)
        self.assertEqual(
            (
                result.request.package,
                result.request.current_version,
                result.request.target_version,
            ),
            ("example-pkg", "1.00", "2.0"),
        )
        self.assertEqual(result.checked_versions, ("3.0", "2.0"))
        self.assertEqual(
            [
                (entry.version, entry.yanked_reason)
                for entry in result.excluded_versions
                if entry.reason == "yanked"
            ],
            [("3.0", "broken")],
        )
        self.assertEqual(
            [call.args for call in self.provider.get_package_info.await_args_list],
            [("example-pkg", "3.0"), ("example-pkg", "2.0")],
        )

    async def test_all_yanked_and_empty_yank_reason(self):
        self.provider.get_package_info.side_effect = lambda name, version: metadata(
            version, yanked=True, yanked_reason=""
        )
        result = await select_latest_stable_update(dependency(), self.provider)
        self.assertEqual(result.status, "no_update")
        self.assertEqual(result.checked_versions, ("3.0", "2.0"))
        self.assertEqual(
            [
                entry.yanked_reason
                for entry in result.excluded_versions
                if entry.reason == "yanked"
            ],
            ["", ""],
        )
        self.assertIsNone(result.request)
        self.assertIsNone(result.target_metadata)
