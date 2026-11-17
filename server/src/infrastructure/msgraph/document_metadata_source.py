from collections.abc import AsyncIterator
from datetime import datetime
from itertools import islice
from typing import cast, override
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient
from pydantic import BaseModel, Field

from domain.exceptions.metadata_inventory_errors import MetadataSourceError
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    AuditActor,
    AuditEntry,
    Document,
    DocumentId,
    DocumentMetadata,
    FileHashes,
    FileMetadata,
)
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
    DocumentMetadataSource,
)
from infrastructure.msgraph.base import raise_for_graph_error, validate_graph_url


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


class GraphDriveItem(BaseModel):
    id: str
    name: str | None = None
    file: GraphFileFacet | None = None
    deleted: dict[str, object] | None = None
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


def _audit_actor(identities: GraphIdentitySet | None) -> AuditActor | None:
    if identities is not None:
        for identity in (identities.user, identities.application, identities.device):
            if (
                identity is not None
                and identity.display_name
                and identity.display_name.strip()
            ):
                return AuditActor(display_name=identity.display_name)
    return None


class SharePointDocumentMetadataSource(DocumentMetadataSource):
    def __init__(self, client: GraphServiceClient) -> None:
        self.client: GraphServiceClient = client

    def _document(self, drive_id: str, item: GraphDriveItem) -> Document:
        assert item.file is not None
        assert item.name is not None
        hashes = item.file.hashes
        return Document(
            _id=DocumentId(drive_id, item.id),
            file_metadata=FileMetadata(
                name=item.name,
                mime_type=item.file.mime_type,
                size_bytes=item.size,
                content_version=ContentVersion(item.c_tag),
                source_url=item.web_url,
                hashes=FileHashes(
                    sha1=hashes.sha1 if hashes else None,
                    sha256=hashes.sha256 if hashes else None,
                    quick_xor=hashes.quick_xor if hashes else None,
                ),
            ),
            document_metadata=DocumentMetadata(
                created=(
                    AuditEntry(
                        timestamp=item.created_date_time,
                        actor=_audit_actor(item.created_by),
                    )
                    if item.created_date_time is not None
                    else None
                ),
                updated=(
                    AuditEntry(
                        timestamp=item.last_modified_date_time,
                        actor=_audit_actor(item.last_modified_by),
                    )
                    if item.last_modified_date_time is not None
                    else None
                ),
            ),
        )

    @override
    async def stream_metadata(
        self,
        drive_id: str,
        extensions: list[str],
        limit: int | None,
        path: str | None = None,
    ) -> AsyncIterator[DocumentMetadataItem]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")

        base_url = f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}"
        item_id = "root"
        if path:
            encoded_path = "/".join(
                quote(segment, safe="") for segment in path.split("/")
            )
            folder_url = f"{base_url}/root:/{encoded_path}:"
            response = cast(
                Response | None,
                await self.client.drives.by_drive_id(drive_id)
                .root.with_url(folder_url)
                .get(
                    request_configuration=RequestConfiguration(
                        options=[ResponseHandlerOption(NativeResponseHandler())]
                    )
                ),
            )
            if response is None:
                raise ValueError("Graph returned no folder metadata")
            raise_for_graph_error(response, "Graph rejected the folder path")
            folder = GraphFolder.model_validate_json(response.content)
            if folder.folder is None:
                raise MetadataSourceError(400, "path must refer to a folder")
            item_id = folder.id

        delta = (
            self.client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(item_id)
            .delta
        )
        delta_path = "root" if path is None else f"items/{quote(item_id, safe='')}"
        next_url: str | None = f"{base_url}/{delta_path}/delta"
        seen_urls: set[str] = set()
        matching_items: dict[str, GraphDriveItem] = {}

        while next_url is not None:
            if next_url in seen_urls:
                raise ValueError("Graph repeated an @odata.nextLink")
            seen_urls.add(next_url)
            validate_graph_url(next_url)

            response = cast(
                Response | None,
                await delta.with_url(next_url).get(
                    request_configuration=RequestConfiguration(
                        options=[ResponseHandlerOption(NativeResponseHandler())]
                    )
                ),
            )
            if response is None:
                raise ValueError("Graph returned no drive item page")
            raise_for_graph_error(response, "Graph rejected the drive metadata request")

            page = GraphDeltaPage.model_validate_json(response.content)
            for item in page.value:
                if (
                    item.deleted is not None
                    or item.file is None
                    or not item.name
                    or (
                        extensions
                        and item.name.rpartition(".")[2].lower() not in extensions
                    )
                ):
                    _ = matching_items.pop(item.id, None)
                else:
                    matching_items[item.id] = item

            next_url = page.next_link
            if next_url is None and page.delta_link is None:
                raise ValueError("Graph did not complete the drive delta scan")

        available = len(matching_items)
        total = min(available, limit) if limit is not None else available
        for item in islice(matching_items.values(), total):
            yield DocumentMetadataItem(self._document(drive_id, item), total, available)
