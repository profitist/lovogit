import unittest
from unittest.mock import Mock, create_autospec

from code_symbol_index import Repository

from lovogit.agent.tools.repo_search import create_repo_search_tools
from lovogit.index.api import Toolkit
from lovogit.index.builder import RepoIndex


class RepoSearchToolsTests(unittest.TestCase):
    def test_tools_expose_json_schemas_without_repository_state(self):
        methods = {
            name
            for name, method in vars(Toolkit).items()
            if hasattr(method, "__wrapped__")
        }
        tools = create_repo_search_tools(Toolkit(Mock(spec=RepoIndex, index=None)))
        self.assertEqual({item.name for item in tools}, methods | {"get_tree"})
        for item in tools:
            with self.subTest(tool=item.name):
                schema = item.args_schema.model_json_schema()
                self.assertTrue(item.description)
                self.assertNotIn("self", schema["properties"])
                self.assertNotIn("index", schema["properties"])

    def test_invocation_validates_arguments_and_uses_bound_repository(self):
        first = create_autospec(Repository, instance=True)
        second = create_autospec(Repository, instance=True)
        first.search_text.return_value = "first repository"
        second.search_text.return_value = "second repository"
        first_tools = {
            item.name: item
            for item in create_repo_search_tools(
                Toolkit(Mock(spec=RepoIndex, index=first))
            )
        }
        second_tools = {
            item.name: item
            for item in create_repo_search_tools(
                Toolkit(Mock(spec=RepoIndex, index=second))
            )
        }
        arguments = {"query": ["target"], "language": "python", "limit": 3}
        self.assertEqual(
            first_tools["search_text"].invoke(arguments), "first repository"
        )
        self.assertEqual(
            second_tools["search_text"].invoke(arguments), "second repository"
        )
        for repo in (first, second):
            forwarded = repo.search_text.call_args.kwargs
            self.assertEqual(list(forwarded["query"]), ["target"])
            self.assertEqual(forwarded["language"], "python")
            self.assertEqual(forwarded["limit"], 3)

    def test_missing_index_error_reaches_tool_caller(self):
        tools = {
            item.name: item
            for item in create_repo_search_tools(
                Toolkit(Mock(spec=RepoIndex, index=None))
            )
        }
        with self.assertRaisesRegex(RuntimeError, "outline"):
            tools["outline"].invoke({"path": "example.py"})

    def test_tree_tool_uses_repo_index(self):
        repo_index = Mock(spec=RepoIndex, tree="repository tree")
        tools = {
            item.name: item for item in create_repo_search_tools(Toolkit(repo_index))
        }
        self.assertEqual(tools["get_tree"].invoke({}), "repository tree")
