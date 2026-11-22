from collections.abc import AsyncIterator
from typing import cast, override
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient

from domain.exceptions.metadata_ingest_errors import MetadataSourceError
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
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
    DocumentMetadataScan,
    DocumentMetadataSource,
    DocumentRefreshSource,
)
from infrastructure.msgraph.base import raise_for_graph_error
from infrastructure.msgraph.document_metadata_models import (
    GraphDeltaPage,
    GraphDriveItem,
    GraphFileFacet,
    GraphFileHashes,
    GraphFolder,
    GraphIdentity,
    GraphIdentitySet,
    GraphParentReference,
)

__all__ = [
    "GraphDeltaPage",
    "GraphDriveItem",
    "GraphFileFacet",
    "GraphFileHashes",
    "GraphFolder",
    "GraphIdentity",
    "GraphIdentitySet",
    "GraphParentReference",
    "SharePointDocumentMetadataSource",
]


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


class SharePointDocumentMetadataSource(DocumentMetadataSource, DocumentRefreshSource):
    def __init__(self, client: GraphServiceClient) -> None:
        self.client: GraphServiceClient = client

    async def _folder_id(
        self, drive_id: str, path: str, root_item_id: str = "root"
    ) -> str:
        base_url = f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}"
        encoded_path = "/".join(quote(segment, safe="") for segment in path.split("/"))
        root_path = (
            "root"
            if root_item_id == "root"
            else f"items/{quote(root_item_id, safe='')}"
        )
        folder_url = (
            f"{base_url}/{root_path}:/{encoded_path}:"
            if path
            else f"{base_url}/{root_path}"
        )
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
        return folder.id

    async def folder_id(self, drive_id: str, path: str, root_item_id: str) -> str:
        return await self._folder_id(drive_id, path, root_item_id)

    def _document(
        self, drive_id: str, item: GraphDriveItem, relative_path: str | None = None
    ) -> Document:
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
                relative_path=relative_path,
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

    def document(
        self, drive_id: str, item: GraphDriveItem, relative_path: str | None
    ) -> Document:
        return self._document(drive_id, item, relative_path)

    @override
    async def scan_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str = "root",
        cursor: str | None = None,
        capture_relative_paths: bool = False,
    ) -> DocumentMetadataScan:
        from infrastructure.msgraph.document_metadata_scan import (
            scan_document_metadata,
        )

        return await scan_document_metadata(
            self, drive_id, selection, root_item_id, cursor, capture_relative_paths
        )

    @override
    async def stream_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str = "root",
    ) -> AsyncIterator[DocumentMetadataItem]:
        scan = await self.scan_metadata(
            drive_id,
            selection,
            root_item_id,
            None,
        )
        total = len(scan.documents)
        for document in scan.documents:
            yield DocumentMetadataItem(document, total, total)
