import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, create_autospec

from code_symbol_index import Repository, SymbolNotFoundError, supported_languages

from lovogit.index.api import Toolkit, get_toolkit
from lovogit.index.builder import RepoIndex


class ToolkitTests(unittest.TestCase):
    @unittest.skipUnless("python" in supported_languages(), "python parser unavailable")
    def test_read_tools_use_real_index(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.py").write_text(
                "def target():\n    return 1\n\ndef caller():\n    return target()\n"
            )
            repo = Repository(
                root=root,
                db_path=root / "index.db",
                languages=["python"],
                create_index=True,
            )
            try:
                repo.build()
                toolkit = Toolkit(Mock(spec=RepoIndex, index=repo))
                filters = {
                    "language": "python",
                    "path": "example.py",
                    "exact_only": True,
                }
                self.assertEqual(
                    toolkit.search_symbols("target", **filters)[0].name, "target"
                )
                self.assertEqual(
                    toolkit.search(["target"], **filters)[0].name, "target"
                )
                self.assertEqual(
                    toolkit.search_page("target", **filters).items[0].name, "target"
                )
                self.assertEqual(
                    toolkit.best_symbol("target", **filters).name, "target"
                )
                self.assertEqual(
                    toolkit.symbol_context("target", anchors=True).definition.name,
                    "target",
                )
                self.assertEqual(
                    toolkit.inspect_symbol("target").definition.name, "target"
                )
                self.assertTrue(toolkit.references("target", 1).items)
                self.assertFalse(toolkit.references("target", offset=100).items)
                self.assertFalse(toolkit.implementations("target", offset=100).items)
                self.assertIsNotNone(toolkit.callers("target", depth=1))
                self.assertIsNotNone(toolkit.callees("caller", depth=1, loose=True))
                self.assertEqual(
                    len(toolkit.outline("example.py", max_symbols=1).items), 1
                )
                self.assertIn("target", toolkit.outline_text("example.py"))
                self.assertIn("target", toolkit.inspect_text("target"))
                self.assertIn("target", toolkit.search_text(["target"]))
                with self.assertRaises(SymbolNotFoundError):
                    toolkit.best_symbol("missing_symbol", exact_only=True)
            finally:
                repo.storage.connection.close()

    def test_tools_require_index_and_preserve_metadata(self):
        toolkit = Toolkit(Mock(spec=RepoIndex, index=None))
        for name, method in vars(Toolkit).items():
            if hasattr(method, "__wrapped__"):
                with self.subTest(method=name):
                    self.assertEqual(method.__name__, name)
                    self.assertTrue(method.__doc__)
                    with self.assertRaisesRegex(RuntimeError, name):
                        getattr(toolkit, name)("query")

    def test_all_tools_forward_parameters_and_results(self):
        repo = create_autospec(Repository, instance=True)
        toolkit = Toolkit(Mock(spec=RepoIndex, index=repo))
        targets = {
            "symbol_context": "inspect",
            "references": "refs",
            "implementations": "impls",
        }
        values = {
            "query": "target",
            "path": "example.py",
            "kind": "function",
            "language": "python",
            "exact_only": True,
            "limit": 7,
            "offset": 3,
            "anchors": True,
            "max_source_chars": 100,
            "ref_kinds": None,
            "depth": 2,
            "loose": True,
            "options": None,
            "symbol": "target",
            "max_symbols": 5,
        }
        for name, method in vars(Toolkit).items():
            if hasattr(method, "__wrapped__") and name != "read_file":
                with self.subTest(method=name):
                    arguments = {
                        parameter: values[parameter]
                        for parameter in inspect.signature(method).parameters
                        if parameter != "self"
                    }
                    target = getattr(repo, targets.get(name, name))
                    result = getattr(toolkit, name)(**arguments)
                    self.assertIs(result, target.return_value)
                    target.assert_called_once_with(**arguments)

    def test_factory_file_reading_and_tree(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.py").write_text("first\nsecond\nthird\n", encoding="utf-8")
            (root / "empty.py").write_text("", encoding="utf-8")
            toolkit = get_toolkit(str(root), str(root / "index.db"))
            try:
                self.assertIsInstance(toolkit.repository, Repository)
                self.assertEqual(toolkit.read_file("example.py", 2, 2), "second")
                self.assertEqual(toolkit.read_file("example.py", 2), "second\nthird")
                self.assertEqual(toolkit.read_file("empty.py"), "")
                self.assertEqual(toolkit.read_file("example.py", 20), "")
                self.assertIn("example.py", toolkit.get_tree())
                for start, end in ((0, None), (1, 0), (3, 2)):
                    with (
                        self.subTest(start=start, end=end),
                        self.assertRaises(ValueError),
                    ):
                        toolkit.read_file("example.py", start, end)
                with self.assertRaises(ValueError):
                    toolkit.read_file("../outside.py")
                with TemporaryDirectory() as outside:
                    (root / "outside.py").symlink_to(Path(outside) / "file.py")
                    with self.assertRaises(ValueError):
                        toolkit.read_file("outside.py")
            finally:
                toolkit.repository.storage.connection.close()
