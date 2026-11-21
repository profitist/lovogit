import tempfile
import unittest
from pathlib import Path

from lovogit.app.dependencies.dto import (
    DependencyDeclarationDTO,
    DependencyInventoryDTO,
    InventoryLimits,
    ProvenanceDTO,
)
from lovogit.app.providers.github.dto import ResolvedRepoDTO, SourceSnapshotDTO

REPO = ResolvedRepoDTO(
    1, "team/project", "https://github.com/team/project", "main", "a" * 40
)


class InventoryDTOTests(unittest.TestCase):
    def test_unknown_version_and_provenance_survive_snapshot_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements.txt").write_text("httpx>=0.27\n")
            dependency = DependencyDeclarationDTO(
                "httpx",
                "httpx",
                "httpx>=0.27",
                ">=0.27",
                (),
                None,
                "runtime",
                (ProvenanceDTO("requirements.txt", start_line=1, end_line=1),),
            )
            result = DependencyInventoryDTO(
                REPO, "requirements", "complete", dependencies=(dependency,)
            )
        self.assertFalse(root.exists())
        self.assertIsNone(result.dependencies[0].current_version)
        self.assertEqual(result.dependencies[0].version_state, "unresolved")
        self.assertEqual(result.dependencies[0].origins[0].start_line, 1)
        self.assertEqual(result.repo.commit_sha, "a" * 40)

    def test_limits_reject_invalid_values(self):
        for name in vars(InventoryLimits()):
            for value in (0, -1, True, 1.5):
                with (
                    self.subTest(name=name, value=value),
                    self.assertRaises(ValueError),
                ):
                    InventoryLimits(**{name: value})


class SourceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def select(self, source="auto"):
        from lovogit.app.dependencies.files import SnapshotReader
        from lovogit.app.dependencies.inventory import select_source

        return select_source(SnapshotReader(self.root, InventoryLimits()), source)

    def test_precedence_empty_project_and_explicit_source(self):
        (self.root / "pyproject.toml").write_text('[project]\nname="example"\n')
        (self.root / "requirements.txt").write_text("requests==2.32.0\n")
        (self.root / "uv.lock").write_text("version=1\n")
        result = self.select()
        self.assertEqual(result.source, "pyproject")
        self.assertEqual(
            {f.path: f.disposition for f in result.files}["requirements.txt"],
            "not_selected",
        )
        result = self.select("requirements")
        self.assertEqual(result.source, "requirements")
        self.assertEqual(
            {f.path: f.disposition for f in result.files}["uv.lock"], "not_selected"
        )

    def test_tool_only_falls_back_but_invalid_toml_does_not(self):
        path = self.root / "pyproject.toml"
        path.write_text("[tool.ruff]\nline-length=88\n")
        (self.root / "requirements.txt").write_text("httpx>=0.27\n")
        self.assertEqual(self.select().source, "requirements")
        path.write_text("[broken")
        result = self.select()
        self.assertTrue(result.failed)
        self.assertEqual(result.source, "pyproject")
        self.assertEqual(result.diagnostics[0].code, "invalid_toml")

    def test_missing_explicit_source_and_no_recursive_discovery(self):
        (self.root / "nested").mkdir()
        (self.root / "nested/requirements.txt").write_text("httpx==0.28.0")
        self.assertIsNone(self.select().source)
        self.assertTrue(self.select("requirements").failed)
        self.assertTrue(self.select("pyproject").failed)
        with self.assertRaises(ValueError):
            self.select("all")

    def test_poetry_is_not_pep621(self):
        (self.root / "pyproject.toml").write_text(
            '[tool.poetry.dependencies]\nhttpx="^0.28"'
        )
        result = self.select()
        self.assertIsNone(result.source)
        self.assertEqual(result.diagnostics[0].code, "unsupported_poetry")


class InventoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.snapshot = SourceSnapshotDTO(REPO, self.root)

    def read(self, **kwargs):
        from lovogit.app.dependencies.inventory import read_dependency_inventory

        return read_dependency_inventory(self.snapshot, **kwargs)

    def test_empty_no_sources_range_and_exact_pin(self):
        self.assertEqual(self.read().status, "no_sources")
        (self.root / "requirements.txt").write_text("")
        self.assertEqual(
            (self.read().status, self.read().dependencies), ("complete", ())
        )
        (self.root / "requirements.txt").write_text(
            "httpx>=0.27,<1\nrequests==2.32.0\n"
        )
        result = self.read()
        self.assertEqual(result.status, "complete")
        self.assertEqual(
            [item.current_version for item in result.dependencies], [None, "2.32.0"]
        )
        self.assertEqual(self.read(), result)

    def test_partial_line_error_and_failed_root(self):
        path = self.root / "requirements.txt"
        path.write_text("httpx>=0.27\n%%%\n")
        result = self.read()
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.dependencies[0].name, "httpx")
        self.assertEqual(result.diagnostics[0].origin.start_line, 2)
        (self.root / "pyproject.toml").write_text("[broken")
        result = self.read()
        self.assertEqual((result.status, result.source), ("failed", "pyproject"))
        path.write_bytes(b"\xff")
        self.assertEqual(self.read(source="requirements").status, "failed")

    def test_public_pep621_with_lock_and_explicit_requirements(self):
        from test_dependency_lock import LOCK_TEXT

        (self.root / "pyproject.toml").write_text(
            '[project]\nname="project"\nrequires-python=">=3.11"\ndependencies=["httpx>=0.27,<1"]\n'
        )
        (self.root / "uv.lock").write_text(LOCK_TEXT)
        (self.root / "requirements.txt").write_text("requests==2.32.0")
        result = self.read()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.dependencies[0].version_state, "locked")
        result = self.read(source="requirements")
        self.assertEqual([item.name for item in result.dependencies], ["requests"])
        self.assertNotIn("uv.lock", [item.path for item in result.read_files])

    def test_dynamic_unsupported_and_budget_are_partial(self):
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="project"\ndynamic=["dependencies"]'
        )
        self.assertEqual(self.read().status, "partial")
        (self.root / "requirements.txt").write_text("pkg===custom\n")
        self.assertEqual(self.read(source="requirements").status, "partial")
        (self.root / "requirements.txt").write_text("httpx>=0.27\nrequests>=2\n")
        result = self.read(source="requirements", limits=InventoryLimits(max_records=1))
        self.assertEqual(result.status, "partial")
        self.assertEqual(len(result.dependencies), 1)

    def test_markers_are_not_evaluated_on_the_controller(self):
        from unittest.mock import patch

        (self.root / "requirements.txt").write_text(
            'pkg==1;sys_platform=="linux"\npkg==2;sys_platform=="win32"\n'
        )
        with patch(
            "packaging.markers.default_environment",
            side_effect=AssertionError("Must not evaluate host"),
        ):
            result = self.read()
        self.assertEqual(
            {item.current_version for item in result.dependencies}, {"1", "2"}
        )
        self.assertEqual(result.status, "complete")


class MalformedManifestTests(unittest.TestCase):
    def test_nested_toml_does_not_escape_inventory(self):
        from lovogit.app.dependencies.inventory import read_dependency_inventory

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("x=" + "[" * 2000 + "1" + "]" * 2000)
            result = read_dependency_inventory(SourceSnapshotDTO(REPO, root))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.diagnostics[0].code, "invalid_toml")


class OversizedTomlNumberTests(unittest.TestCase):
    def test_integer_conversion_limit_becomes_a_file_diagnostic(self):
        from lovogit.app.dependencies.inventory import read_dependency_inventory

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("x=" + "9" * 5000)
            result = read_dependency_inventory(SourceSnapshotDTO(REPO, root))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.diagnostics[0].code, "invalid_toml")
