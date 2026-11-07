"""Public Teams API responses."""

from pydantic import BaseModel, Field, field_validator


class TeamsQuery(BaseModel):
    name: str | None = Field(
        default=None, description="Case-insensitive substring of the Team name."
    )


class TeamFilesQuery(BaseModel):
    path: str | None = Field(
        default=None,
        description="Case-insensitive path prefix relative to a document library root.",
    )
    name: str | None = Field(
        default=None, description="Case-insensitive substring of the file name."
    )
    recursive: bool = Field(
        default=False, description="Include files in nested folders."
    )
    limit: int = Field(
        default=100, ge=1, le=500, description="Maximum number of matching files."
    )

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip("/")
        if (
            not value
            or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("path must be relative to a library root")
        return value


class TeamResponse(BaseModel):
    id: str
    display_name: str


class TeamFileResponse(BaseModel):
    library: str
    drive_id: str
    item_id: str
    name: str
    path: str
    type: str
    size: int | None = None
    web_url: str | None = None


class TeamErrorResponse(BaseModel):
    detail: str
