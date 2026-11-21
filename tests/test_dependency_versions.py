import unittest

from lovogit.app.dependencies.dto import ProvenanceDTO
from lovogit.app.dependencies.versions import classify_versions, parse_requirement


def requirement(raw, line=1, **kwargs):
    return parse_requirement(
        raw, ProvenanceDTO("requirements.txt", start_line=line, end_line=line), **kwargs
    )


class DependencyVersionTests(unittest.TestCase):
    def test_ranges_wildcards_and_exact_versions(self):
        for raw, state, version in (
            ("pkg>=1,<3", "unresolved", None),
            ("pkg==1.*", "unresolved", None),
            ("pkg", "unresolved", None),
            ("pkg==1.2", "declared_pin", "1.2"),
            ("pkg===1.2", "declared_pin", "1.2"),
            ("pkg===custom", "unsupported", None),
            ("pkg==1.0rc1", "declared_pin", "1.0rc1"),
        ):
            with self.subTest(raw=raw):
                result = classify_versions([requirement(raw)])[0]
                self.assertEqual(
                    (result.version_state, result.current_version), (state, version)
                )

    def test_constraints_pin_without_creating_dependencies(self):
        result = classify_versions(
            [requirement("Some_Pkg>=1,<3")],
            [requirement("some-pkg==2", 2), requirement("unused==9", 3)],
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].current_version, "2")
        self.assertEqual(result[0].constraint_origins[0].start_line, 2)
        self.assertEqual(result[0].origins[0].start_line, 1)

    def test_conflict_aliases_duplicates_and_compatible_bounds(self):
        result = classify_versions(
            [requirement("Some_Pkg==2"), requirement("some-pkg<2", 2)]
        )
        self.assertTrue(
            all(
                item.version_state == "conflict" and item.current_version is None
                for item in result
            )
        )
        result = classify_versions(
            [requirement("pkg==2"), requirement("pkg==2", 3), requirement("pkg>=1", 2)]
        )
        self.assertTrue(all(item.current_version == "2" for item in result))
        self.assertEqual([o.start_line for o in result[0].origins], [1, 2, 3])

    def test_markers_and_optional_scopes_are_not_merged(self):
        result = classify_versions(
            [
                requirement('pkg==1; sys_platform=="linux"'),
                requirement('pkg==2; sys_platform=="win32"'),
                requirement("pkg==3", scope="extra:test"),
            ]
        )
        self.assertEqual({item.current_version for item in result}, {"1", "2", "3"})
        self.assertEqual(len(result), 3)
        result = classify_versions(
            [requirement("pkg>=1")], [requirement('pkg==2; sys_platform=="linux"')]
        )
        self.assertIsNone(result[0].current_version)
        self.assertEqual(result[0].reason, "conditional_constraints")

    def test_non_registry_sources_and_editables(self):
        for raw, editable in (
            ("pkg @ https://example.org/pkg-1.whl", False),
            ("pkg @ git+https://example.org/repo", False),
            ("pkg @ file:///project", False),
            ("git+https://example.org/repo#egg=pkg", True),
        ):
            result = classify_versions([requirement(raw, editable=editable)])[0]
            self.assertEqual(result.name, "pkg")
            self.assertEqual(result.version_state, "unsupported")
            self.assertIsNone(result.current_version)
        with self.assertRaises(ValueError):
            requirement("./local", editable=True)


class MalformedRequirementTests(unittest.TestCase):
    def test_oversized_numeric_version_is_unsupported(self):
        result = classify_versions([requirement("pkg==" + "9" * 5000)])[0]
        self.assertEqual(result.version_state, "unsupported")
        self.assertIsNone(result.current_version)

    def test_deep_marker_reports_invalid_requirement(self):
        with self.assertRaises(ValueError):
            requirement("pkg; " + "(" * 2000 + 'os_name == "posix"' + ")" * 2000)
