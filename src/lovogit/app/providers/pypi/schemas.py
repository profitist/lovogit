from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _PyPiApiSchema(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class IndexMetaApiSchema(_PyPiApiSchema):
    api_version: str = Field(alias="api-version")

    @field_validator("api_version")
    @classmethod
    def _supported_version(cls, value: str) -> str:
        major, minor = value.split(".")
        if int(major) != 1 or int(minor) < 1:
            raise ValueError("PyPI index API 1.1 or newer within major 1 is required")
        return value


class PackageFileApiSchema(_PyPiApiSchema):
    filename: str = Field(min_length=1)
    url: str = Field(min_length=1)
    hashes: dict[str, str]
    requires_python: str | None = Field(default=None, alias="requires-python")
    yanked: bool | str = False
    size: int = Field(ge=0)
    uploaded_at: datetime | None = Field(default=None, alias="upload-time")


class PackageIndexApiSchema(_PyPiApiSchema):
    meta: IndexMetaApiSchema
    name: str = Field(min_length=1)
    versions: list[str]
    files: list[PackageFileApiSchema]


class PackageInfoApiSchema(_PyPiApiSchema):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    summary: str | None = None
    description: str | None = None
    description_content_type: str | None = None
    requires_python: str | None = None
    requires_dist: list[str] | None = None
    dynamic: list[str] | None = None
    project_urls: dict[str, str] | None = None
    home_page: str | None = None
    package_url: str | None = None
    release_url: str | None = None
    yanked: bool = False
    yanked_reason: str | None = None


class PackageResponseApiSchema(_PyPiApiSchema):
    info: PackageInfoApiSchema
