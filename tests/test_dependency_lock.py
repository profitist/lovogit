import copy
import tempfile
import unittest
from pathlib import Path

from lovogit.app.dependencies.dto import InventoryLimits
from lovogit.app.dependencies.files import SnapshotReader
from lovogit.app.dependencies.uv_lock import read_uv_lock

LOCK_TEXT = """version = 1
revision = 3
requires-python = ">=3.11"
[[package]]
name = "project"
version = "0.1.0"
source = {virtual = "."}
dependencies = [{name = "httpx"}]
[package.metadata]
requires-dist = [{name = "httpx", specifier = ">=0.27,<1"}]
[[package]]
name = "httpx"
version = "0.28.0"
source = {registry = "https://pypi.org/simple"}
"""
PROJECT = {
    "project": {
        "name": "project",
        "requires-python": ">=3.11",
        "dependencies": ["httpx>=0.27,<1"],
    }
}


class LockFixtures(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def read(self, text=LOCK_TEXT, project=None):
        (self.root / "uv.lock").write_text(text)
        return read_uv_lock(
            SnapshotReader(self.root, InventoryLimits()), project or PROJECT
        )


class LockLayoutTests(LockFixtures):
    def test_virtual_and_editable_root(self):
        for source in ("virtual", "editable"):
            result = self.read(LOCK_TEXT.replace("virtual", source))
            self.assertEqual(result.root_index, 0)
            self.assertEqual(result.diagnostics, ())

    def test_unknown_format_invalid_toml_and_ambiguous_root(self):
        for text, code in (
            ("[broken", "invalid_lock_toml"),
            (
                LOCK_TEXT.replace("revision = 3", "revision = 99"),
                "unsupported_lock_version",
            ),
            (
                LOCK_TEXT.replace("version = 1\n", "version = true\n", 1),
                "unsupported_lock_version",
            ),
            (
                LOCK_TEXT + '\n[[package]]\nname="other"\nsource={editable="."}\n',
                "ambiguous_lock_root",
            ),
        ):
            with self.subTest(code=code):
                result = self.read(text)
                self.assertIsNone(result.root_index)
                self.assertEqual(result.diagnostics[0].code, code)

    def test_workspace_and_missing_root(self):
        result = self.read(LOCK_TEXT + '\n[manifest]\nmembers=["project", "other"]\n')
        self.assertEqual(result.diagnostics[0].code, "unsupported_workspace")
        project = copy.deepcopy(PROJECT)
        project["tool"] = {"uv": {"workspace": {"members": ["packages/*"]}}}
        self.assertEqual(
            self.read(project=project).diagnostics[0].code, "unsupported_workspace"
        )
        self.assertEqual(
            self.read(LOCK_TEXT.replace('virtual = "."', 'virtual = "other"'))
            .diagnostics[0]
            .code,
            "missing_lock_root",
        )


class LockMetadataTests(LockFixtures):
    def validate(self, project=None, text=LOCK_TEXT):
        from lovogit.app.dependencies.pyproject import parse_pyproject
        from lovogit.app.dependencies.uv_lock import validate_lock_metadata

        project = project or PROJECT
        document = self.read(text, project)
        reader = SnapshotReader(self.root, InventoryLimits())
        parsed = parse_pyproject(project, reader)
        return validate_lock_metadata(document, project, parsed.dependencies, reader)

    def test_equivalent_names_specifiers_and_metadata_order(self):
        project = copy.deepcopy(PROJECT)
        project["project"]["dependencies"] = ["HTTPX<1,>=0.27"]
        self.assertEqual(self.validate(project), ())

    def test_stale_ranges_and_python_requirement(self):
        project = copy.deepcopy(PROJECT)
        project["project"]["dependencies"] = ["httpx>=0.26,<1"]
        self.assertEqual(self.validate(project)[0].code, "lock_metadata_mismatch")
        project = copy.deepcopy(PROJECT)
        project["project"]["requires-python"] = ">=3.12"
        self.assertEqual(self.validate(project)[0].code, "lock_metadata_mismatch")

    def test_missing_metadata_dynamic_and_overrides(self):
        self.assertEqual(
            self.validate(
                text=LOCK_TEXT.replace(
                    'requires-dist = [{name = "httpx", specifier = ">=0.27,<1"}]',
                    "other = []",
                )
            )[0].code,
            "unsupported_lock_metadata",
        )
        for key in ("override-dependencies", "constraint-dependencies", "sources"):
            project = copy.deepcopy(PROJECT)
            project["tool"] = {"uv": {key: []}}
            self.assertEqual(
                self.validate(project)[0].code, "unsupported_uv_configuration"
            )
        project = copy.deepcopy(PROJECT)
        project["project"]["dynamic"] = ["dependencies"]
        self.assertEqual(self.validate(project)[0].code, "dynamic_lock_metadata")

    def test_optional_extras_and_markers(self):
        project = copy.deepcopy(PROJECT)
        project["project"]["dependencies"] = [
            'httpx[http2]>=0.27,<1; python_version >= "3.11"'
        ]
        project["project"]["optional-dependencies"] = {"Test_Group": ["pytest>=8"]}
        text = LOCK_TEXT.replace(
            'requires-dist = [{name = "httpx", specifier = ">=0.27,<1"}]',
            """requires-dist = [
{name = "pytest", specifier = ">=8", marker = "extra == 'test-group'"},
{name = "HTTPX", extras = ["http2"], specifier = "<1,>=0.27", marker = "python_version >= '3.11'"}
]""",
        )
        self.assertEqual(self.validate(project, text), ())
        self.assertEqual(
            self.validate(project, text.replace('extras = ["http2"]', "extras = []"))[
                0
            ].code,
            "lock_metadata_mismatch",
        )


class LockCandidateTests(LockFixtures):
    def apply(self, project=None, text=LOCK_TEXT):
        from lovogit.app.dependencies.pyproject import parse_pyproject
        from lovogit.app.dependencies.uv_lock import apply_uv_lock
        from lovogit.app.dependencies.versions import classify_versions

        project = project or PROJECT
        document = self.read(text, project)
        reader = SnapshotReader(self.root, InventoryLimits())
        parsed = parse_pyproject(project, reader)
        return apply_uv_lock(
            classify_versions(parsed.dependencies), document, project, reader
        )

    def test_unique_direct_version_and_transitive_exclusion(self):
        result, diagnostics = self.apply(
            text=LOCK_TEXT
            + '\n[[package]]\nname="httpcore"\nversion="1.0.9"\nsource={registry="https://pypi.org/simple"}\n'
        )
        self.assertEqual(diagnostics, ())
        self.assertEqual(len(result), 1)
        self.assertEqual(
            (result[0].name, result[0].version_state, result[0].current_version),
            ("httpx", "locked", "0.28.0"),
        )
        self.assertIn("uv.lock", {origin.path for origin in result[0].origins})

    def test_missing_edge_or_entry(self):
        for text in (
            LOCK_TEXT.replace('dependencies = [{name = "httpx"}]', "dependencies = []"),
            LOCK_TEXT.replace('name = "httpx"\nversion', 'name = "other"\nversion'),
        ):
            result, _diagnostics = self.apply(text=text)
            self.assertEqual(result[0].reason, "missing_lock_entry")
            self.assertIsNone(result[0].current_version)

    def test_pin_conflict_and_broken_lock_do_not_fall_back(self):
        project = copy.deepcopy(PROJECT)
        project["project"]["dependencies"] = ["httpx==0.27.0"]
        text = LOCK_TEXT.replace('specifier = ">=0.27,<1"', 'specifier = "==0.27.0"')
        result, _ = self.apply(project, text)
        self.assertEqual(result[0].version_state, "conflict")
        result, _ = self.apply(project, "[broken")
        self.assertIsNone(result[0].current_version)
        self.assertEqual(result[0].reason, "invalid_lock_toml")

    def test_variants_markers_and_sources_are_retained(self):
        variant = '\n[[package]]\nname="httpx"\nversion="0.27.2"\nsource={registry="https://pypi.org/simple"}\n'
        result, _ = self.apply(text=LOCK_TEXT + variant)
        self.assertEqual(result[0].reason, "ambiguous_lock")
        self.assertEqual(len(result[0].lock_candidates), 2)
        result, _ = self.apply(
            text=LOCK_TEXT.replace(
                "revision = 3",
                "revision = 3\nresolution-markers=[\"python_version < '3.12'\"]",
            )
        )
        self.assertEqual(result[0].reason, "conditional_lock")
        result, _ = self.apply(
            text=LOCK_TEXT
            + variant.replace('version="0.27.2"', 'version="0.28.0"').replace(
                "https://pypi.org/simple", "https://other.org/simple"
            )
        )
        self.assertEqual(result[0].reason, "ambiguous_lock_source")

    def test_optional_edge_and_extra_are_linked(self):
        project = copy.deepcopy(PROJECT)
        project["project"]["dependencies"] = []
        project["project"]["optional-dependencies"] = {
            "Test": ["httpx[http2]>=0.27,<1"]
        }
        text = LOCK_TEXT.replace(
            'dependencies = [{name = "httpx"}]',
            'dependencies = []\n[package.optional-dependencies]\ntest=[{name="httpx",extra=["http2"]}]',
        ).replace(
            'specifier = ">=0.27,<1"',
            'specifier = ">=0.27,<1", extras=["http2"], marker="extra == \'test\'"',
        )
        result, diagnostics = self.apply(project, text)
        self.assertEqual(diagnostics, ())
        self.assertEqual(result[0].version_state, "locked")
        self.assertEqual(result[0].scope, "extra:test")


class MalformedLockTests(LockFixtures):
    def test_nested_and_oversized_integer_toml(self):
        for text in ("x=" + "[" * 2000 + "1" + "]" * 2000, "x=" + "9" * 5000):
            result = self.read(text)
            self.assertEqual(result.diagnostics[0].code, "invalid_lock_toml")
