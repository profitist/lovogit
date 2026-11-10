from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxLimits(BaseModel):
    cpus: float = Field(default=1.0, gt=0, le=64)
    memory_mb: int = Field(default=1024, ge=64, le=65536)
    pids_limit: int = Field(default=128, ge=16, le=4096)
    timeout_seconds: int = Field(default=120, ge=1, le=86400)
    max_output_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SANDBOX_",
        env_file=".env",
        extra="ignore",
        env_nested_delimiter="__",
    )

    root: Path = Path("output/sandbox")
    image: str = "lovogit-sandbox:local"
    preparation_network: str = "none"
    integration_network: str | None = None
    preparation_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    max_snapshot_bytes: int = Field(default=128_000_000, ge=1024)
    max_artifact_bytes: int = Field(default=10_000_000, ge=1024)
    limits: SandboxLimits = Field(default_factory=SandboxLimits)
