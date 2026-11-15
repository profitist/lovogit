from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=4000)


class ChangeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: Literal[
        "api",
        "behavior",
        "configuration",
        "default_value",
        "python_requirement",
        "dependency_requirement",
    ]
    description: str = Field(min_length=1, max_length=2000)
    symbol: str | None = Field(max_length=300)
    release_version: str | None = Field(max_length=300)
    evidence: list[EvidenceSchema] = Field(min_length=1, max_length=5)
    version_evidence: EvidenceSchema | None


class ExtractionResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    changes: list[Any] = Field(max_length=100)
    limitations: list[str] = Field(max_length=20)
