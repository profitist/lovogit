from typing import NotRequired, TypedDict

from langgraph.graph import MessagesState

from lovogit.agent.comparer.sandbox import SandboxState


class DependencyChange(TypedDict):
    package: str
    current_version: str
    target_version: str
    changed_api: str
    change_description: str


class CodeLocation(TypedDict):
    file: str
    symbol: str | None
    line: int
    reason: str


class ContextResult(TypedDict):
    usages: list[CodeLocation]
    related_tests: list[str]
    analysis_complete: bool


class TestCase(TypedDict):
    id: str
    file_path: str
    target_symbol: str
    artifact_id: str


class ComparerState(MessagesState, SandboxState):
    # Repository
    repository_id: str
    repository_sha: str

    # Dependency update
    change: DependencyChange

    # Code investigation
    context: NotRequired[ContextResult]

    # Test generation
    tests: NotRequired[list[TestCase]]
    test_attempt: NotRequired[int]
