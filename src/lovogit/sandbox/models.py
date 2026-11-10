from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SandboxDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EnvironmentInfo(SandboxDTO):
    environment_id: str
    workspace_id: str
    repository_id: str
    repository_sha: str
    python_version: str
    dependency_overrides: dict[str, str]
    baseline_environment_id: str | None = None
    status: Literal["preparing", "ready", "blocked", "deleted"]
    fingerprint: str
    source_hash: str
    lockfile_hash: str | None = None
    image_digest: str = ""
    platform: str = ""
    installed_dependencies: dict[str, str] = Field(default_factory=dict)
    dependency_changes: dict[str, tuple[str | None, str | None]] = Field(
        default_factory=dict
    )
    variables: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    preparation_artifact_id: str | None = None


class TestArtifact(SandboxDTO):
    path: str
    sha256: str
    artifact_id: str


class TestBundle(SandboxDTO):
    bundle_id: str
    repository_id: str
    repository_sha: str
    source_hash: str
    tests: tuple[TestArtifact, ...]
    fingerprint: str
    created_at: datetime


class TestFailure(SandboxDTO):
    node_id: str
    phase: Literal["setup", "call", "teardown", "collect"]
    error_type: str
    message: str
    traceback: str
    traceback_artifact_id: str
    duration_seconds: float


RunStatus = Literal[
    "running",
    "passed",
    "failed",
    "collection_error",
    "no_tests",
    "timeout",
    "infrastructure_error",
    "preparation_error",
    "resource_limit",
    "interrupted",
]


class TestRunResult(SandboxDTO):
    run_id: str
    environment_id: str
    test_bundle_id: str
    status: RunStatus
    exit_code: int | None = None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration_seconds: float = 0
    report_artifact_id: str | None = None
    coverage_artifact_id: str | None = None
    stdout_artifact_id: str | None = None
    failures: list[TestFailure] = Field(default_factory=list)
    outcomes: dict[str, str] = Field(default_factory=dict)
    phases: dict[str, dict[str, str]] = Field(default_factory=dict)
    network: str = "none"
    reason: str | None = None


class CoverageResult(SandboxDTO):
    file: str | None = None
    executed_lines: list[int] = Field(default_factory=list)
    missing_lines: list[int] = Field(default_factory=list)
    executed_branches: list[list[int]] = Field(default_factory=list)
    missing_branches: list[list[int]] = Field(default_factory=list)
    coverage_percent: float = 0


class ComparisonResult(SandboxDTO):
    verdict: Literal[
        "compatible", "breaking", "inconclusive", "blocked", "baseline_failed"
    ]
    explanation: str
    introduced_failures: list[str] = Field(default_factory=list)
    changed_tests: dict[str, tuple[str | None, str | None]] = Field(
        default_factory=dict
    )
    environment_differences: dict[str, object] = Field(default_factory=dict)
    coverage_delta: float | None = None
    coverage_changes: dict[str, list[int]] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
