import tempfile
import unittest
from pathlib import Path

from lovogit.app.dependencies.dto import InventoryLimits
from lovogit.app.dependencies.files import SnapshotReader
from lovogit.app.dependencies.pyproject import parse_pyproject


class PyprojectParsingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.reader = SnapshotReader(Path(self.directory.name), InventoryLimits())

    def test_names_extras_markers_and_scopes(self):
        result = parse_pyproject(
            {
                "project": {
                    "dependencies": ["Some_Pkg[Foo_Bar]>=1; python_version<'3.14'"],
                    "optional-dependencies": {"Test_Group": ["pytest>=8"]},
                }
            },
            self.reader,
        )
        first, second = result.dependencies
        self.assertEqual(
            (first.name, first.extras, first.scope),
            ("some-pkg", ("foo-bar",), "runtime"),
        )
        self.assertEqual(first.marker, 'python_version < "3.14"')
        self.assertEqual(first.origins[0].key, "project.dependencies[0]")
        self.assertEqual(second.scope, "extra:test-group")

    def test_empty_dynamic_and_build_requirements(self):
        self.assertEqual(parse_pyproject({"project": {}}, self.reader).dependencies, ())
        self.assertEqual(
            parse_pyproject({"project": {"dependencies": []}}, self.reader).diagnostics,
            (),
        )
        result = parse_pyproject(
            {
                "project": {"dynamic": ["dependencies"]},
                "build-system": {"requires": ["setuptools"]},
                "dependency-groups": {"dev": ["ruff"]},
            },
            self.reader,
        )
        self.assertEqual(result.dependencies, ())
        self.assertEqual(
            {d.code for d in result.diagnostics},
            {"dynamic_dependencies", "unsupported_dependency_groups"},
        )

    def test_invalid_values_are_independent(self):
        result = parse_pyproject(
            {
                "project": {
                    "dependencies": [42, "%%%", "httpx>=0.27"],
                    "optional-dependencies": {"test": "pytest"},
                }
            },
            self.reader,
        )
        self.assertEqual([d.name for d in result.dependencies], ["httpx"])
        self.assertEqual(len(result.diagnostics), 3)
        self.assertTrue(parse_pyproject({"project": "invalid"}, self.reader).failed)
        result = parse_pyproject(
            {"project": {"dynamic": "dependencies", "dependencies": ["httpx"]}},
            self.reader,
        )
        self.assertIn("invalid_dynamic", {d.code for d in result.diagnostics})
        self.assertEqual(result.dependencies, ())


class RequirementsParsingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "sub").mkdir()

    def parse(self, text, **limits):
        from lovogit.app.dependencies.requirements import parse_requirements

        (self.root / "requirements.txt").write_text(text, encoding="utf-8-sig")
        return parse_requirements(SnapshotReader(self.root, InventoryLimits(**limits)))

    def test_include_forms_constraints_provenance_hashes_and_fragments(self):
        (self.root / "sub/base.txt").write_text(
            'httpx[http2]>=0.27 \\\n --hash=sha256:abc --hash sha256:def ; python_version >= "3.10" # note\n'
        )
        (self.root / "constraints.txt").write_text("urllib3==2.2.3\n")
        for flag in (
            "-r sub/base.txt",
            "-rsub/base.txt",
            "--requirement sub/base.txt",
            "--requirement=sub/base.txt",
        ):
            for constraint in (
                "-c constraints.txt",
                "-cconstraints.txt",
                "--constraint constraints.txt",
                "--constraint=constraints.txt",
            ):
                result = self.parse(
                    f"# comment\n\n{flag}\n{constraint}\npkg @ https://example.org/a.whl#sha256=123\n"
                )
                self.assertEqual(
                    [d.name for d in result.dependencies], ["httpx", "pkg"]
                )
                first = result.dependencies[0]
                self.assertEqual(first.hashes, ("sha256:abc", "sha256:def"))
                self.assertEqual(
                    (first.origins[0].start_line, first.origins[0].end_line), (1, 2)
                )
                self.assertIsNotNone(first.marker)
                self.assertIn("#sha256=123", result.dependencies[1].raw_requirement)
                self.assertEqual([c.name for c in result.constraints], ["urllib3"])

    def test_cycle_repeated_file_and_modes(self):
        (self.root / "sub/base.txt").write_text("httpx>=0.27\n-r ../requirements.txt\n")
        result = self.parse("-r sub/base.txt\n-r sub/base.txt\n-c sub/base.txt\n")
        self.assertEqual(len(result.dependencies), 2)
        self.assertEqual(len(result.constraints), 1)
        self.assertIn("include_cycle", {d.code for d in result.diagnostics})

    def test_independent_errors_and_no_remote_or_environment_expansion(self):
        result = self.parse(
            "-r https://example.org/req.txt\n-r missing\n--index-url https://secret@example.org\nhttpx==${VERSION}\n%%%\nrequests==2.32.0\n"
        )
        self.assertEqual([d.name for d in result.dependencies], ["requests"])
        self.assertEqual(
            {d.code for d in result.diagnostics},
            {
                "forbidden_path",
                "missing_file",
                "unsupported_option",
                "environment_substitution",
                "invalid_requirement",
            },
        )
        self.assertTrue(all("secret" not in repr(d) for d in result.diagnostics))

    def test_depth_records_encoding_and_incomplete_line(self):
        (self.root / "sub/base.txt").write_text("-r second.txt\n")
        (self.root / "sub/second.txt").write_text("httpx==0.28.0\n")
        result = self.parse("-r sub/base.txt\n", max_depth=1)
        self.assertIn("include_depth_limit", {d.code for d in result.diagnostics})
        result = self.parse("httpx>=0.27\nrequests>=2\n", max_records=1)
        self.assertEqual(len(result.dependencies), 1)
        self.assertIn("record_limit", {d.code for d in result.diagnostics})
        result = self.parse("httpx>=0.27 \\\n")
        self.assertEqual(result.dependencies, ())
        self.assertIn("incomplete_continuation", {d.code for d in result.diagnostics})


class QuotedRequirementTests(unittest.TestCase):
    def test_marker_contents_are_not_install_options_or_comments(self):
        from lovogit.app.dependencies.requirements import parse_requirements

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requirements.txt").write_text(
                'pkg==1; os_name == "strange --hash sha256:abc # value" --hash=sha256:real\n'
            )
            result = parse_requirements(SnapshotReader(root, InventoryLimits()))
        self.assertEqual(result.diagnostics, ())
        self.assertEqual(result.dependencies[0].hashes, ("sha256:real",))
        self.assertIn(
            "strange --hash sha256:abc # value", result.dependencies[0].marker
        )
