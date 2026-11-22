from datetime import datetime

from pydantic import BaseModel, Field


class GraphFileHashes(BaseModel):
    sha1: str | None = Field(default=None, alias="sha1Hash")
    sha256: str | None = Field(default=None, alias="sha256Hash")
    quick_xor: str | None = Field(default=None, alias="quickXorHash")


class GraphFileFacet(BaseModel):
    mime_type: str | None = Field(default=None, alias="mimeType")
    hashes: GraphFileHashes | None = None


class GraphIdentity(BaseModel):
    display_name: str | None = Field(default=None, alias="displayName")


class GraphIdentitySet(BaseModel):
    user: GraphIdentity | None = None
    application: GraphIdentity | None = None
    device: GraphIdentity | None = None


class GraphParentReference(BaseModel):
    id: str | None = None


class GraphDriveItem(BaseModel):
    id: str
    name: str | None = None
    file: GraphFileFacet | None = None
    deleted: dict[str, object] | None = None
    folder: dict[str, object] | None = None
    parent_reference: GraphParentReference | None = Field(
        default=None, alias="parentReference"
    )
    c_tag: str | None = Field(default=None, alias="cTag")
    web_url: str | None = Field(default=None, alias="webUrl")
    size: int | None = None
    created_date_time: datetime | None = Field(default=None, alias="createdDateTime")
    created_by: GraphIdentitySet | None = Field(default=None, alias="createdBy")
    last_modified_date_time: datetime | None = Field(
        default=None, alias="lastModifiedDateTime"
    )
    last_modified_by: GraphIdentitySet | None = Field(
        default=None, alias="lastModifiedBy"
    )


class GraphDeltaPage(BaseModel):
    value: list[GraphDriveItem]
    next_link: str | None = Field(default=None, alias="@odata.nextLink")
    delta_link: str | None = Field(default=None, alias="@odata.deltaLink")


class GraphFolder(BaseModel):
    id: str
    folder: dict[str, object] | None = None
