from enum import StrEnum


class RunState(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class DependencyStatus(StrEnum):
    INCOMPAT = "incompat"
    REVIEW = "review"
    FAILED = "failed"
    WAITING = "waiting"
    LOW = "low"
    CURRENT = "current"
