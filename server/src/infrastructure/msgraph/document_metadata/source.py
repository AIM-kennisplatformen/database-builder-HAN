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
from infrastructure.msgraph.base import raise_for_graph_error, validate_graph_url
from infrastructure.msgraph.document_metadata.models import (
    GraphDeltaPage,
    GraphDriveItem,
    GraphFileFacet,
    GraphFileHashes,
    GraphFolder,
    GraphIdentity,
    GraphIdentitySet,
    GraphParentReference,
)
from infrastructure.msgraph.document_metadata.scan import (
    ScanState,
    delta_path,
    read_delta,
    resolve_path_scope,
    select_documents,
)

__all__ = [
    "DocumentMetadataSourceSharePoint",
    "GraphDeltaPage",
    "GraphDriveItem",
    "GraphFileFacet",
    "GraphFileHashes",
    "GraphFolder",
    "GraphIdentity",
    "GraphIdentitySet",
    "GraphParentReference",
]


def _build_audit_actor(identities: GraphIdentitySet | None) -> AuditActor | None:
    if identities is not None:
        for identity in (identities.user, identities.application, identities.device):
            if (
                identity is not None
                and identity.display_name
                and identity.display_name.strip()
            ):
                return AuditActor(display_name=identity.display_name)
    return None


class DocumentMetadataSourceSharePoint(DocumentMetadataSource, DocumentRefreshSource):
    def __init__(self, client: GraphServiceClient) -> None:
        self.client: GraphServiceClient = client

    async def folder_id(self, drive_id: str, path: str, root_item_id: str) -> str:
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

    def document(
        self, drive_id: str, item: GraphDriveItem, relative_path: str | None
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
                        actor=_build_audit_actor(item.created_by),
                    )
                    if item.created_date_time is not None
                    else None
                ),
                updated=(
                    AuditEntry(
                        timestamp=item.last_modified_date_time,
                        actor=_build_audit_actor(item.last_modified_by),
                    )
                    if item.last_modified_date_time is not None
                    else None
                ),
            ),
        )

    @override
    async def scan_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str = "root",
        cursor: str | None = None,
        capture_relative_paths: bool = False,
    ) -> DocumentMetadataScan:
        path_scoped = any(
            rule.path for rule in selection.include_rules + selection.exclude_rules
        )
        state = ScanState(
            client=self.client,
            folder_resolver=self.folder_id,
            document_mapper=self.document,
            drive_id=drive_id,
            selection=selection,
            root_item_id=root_item_id,
            cursor=cursor,
            path_scoped=path_scoped,
            capture_paths=path_scoped or capture_relative_paths,
            root_id=None,
        )
        state = await resolve_path_scope(state)
        base_url = f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}"
        baseline_url = f"{base_url}/{delta_path(root_item_id)}/delta"
        snapshot = await read_delta(state, baseline_url)
        documents, deleted_ids = await select_documents(state, snapshot)
        if snapshot.delta_link is None:
            raise ValueError("Graph did not provide a completed delta cursor")
        validate_graph_url(snapshot.delta_link)
        return DocumentMetadataScan(
            documents=tuple(documents),
            deleted_item_ids=tuple(sorted(deleted_ids)),
            delta_link=snapshot.delta_link,
            baseline=snapshot.baseline,
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
