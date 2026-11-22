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
    """Read selected drive documents and delta snapshots from Microsoft Graph."""

    def __init__(self, client: GraphServiceClient) -> None:
        self.client: GraphServiceClient = client

    async def folder_id(self, drive_id: str, path: str, root_item_id: str) -> str:
        """Resolve a drive-relative path to a folder item ID.

        Args:
            drive_id: Graph drive containing the path.
            path: Slash-separated path relative to ``root_item_id``; an empty path
                resolves the root item itself.
            root_item_id: ``"root"`` or the ID of the folder that anchors the path.

        Returns:
            The Graph item ID for the resolved folder.

        Raises:
            ValueError: If Graph returns no folder metadata.
            MetadataSourceError: If the path resolves to a non-folder or Graph rejects
                the request.
        """
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
        """Convert a Graph file item into the domain document representation.

        Args:
            drive_id: Drive that owns the item.
            item: Graph item with a file facet and name.
            relative_path: Optional path relative to the scanned root.

        Returns:
            A document containing file metadata and available creation/update audit
            entries.
        """
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
        """Read and filter a drive delta snapshot for the requested selection.

        Path-based selections always read a baseline so folder ancestry is complete.
        A supplied cursor is reused only when paths do not need to be resolved.

        Args:
            drive_id: Graph drive to scan.
            selection: Include and exclude rules applied to file items.
            root_item_id: Root item to scan, defaulting to the drive root.
            cursor: Previous Graph delta link, if available.
            capture_relative_paths: Include paths relative to the scan root.

        Returns:
            Selected documents, deleted item IDs, the completed delta cursor, and
            whether the scan used a baseline.

        Raises:
            ValueError: If Graph returns an invalid or incomplete delta sequence or
                incomplete folder ancestry.
            MetadataSourceError: If Graph rejects a request other than an expired
                cursor that can be recovered with a baseline scan.
        """
        path_scoped = any(
            rule.path for rule in selection.include_rules + selection.exclude_rules
        )
        capture_paths = path_scoped or capture_relative_paths
        root_id = None
        if capture_paths:
            root_id = (
                await self.folder_id(drive_id, "", root_item_id)
                if root_item_id == "root"
                else root_item_id
            )
        rule_resolve_folder_ids: dict[str, str] = {}
        if path_scoped:
            paths = {
                rule.path
                for rule in selection.include_rules + selection.exclude_rules
                if rule.path
            }
            for path in paths:
                rule_resolve_folder_ids[path] = await self.folder_id(
                    drive_id, path, root_item_id
                )
        state = ScanState(
            client=self.client,
            document_mapper=self.document,
            drive_id=drive_id,
            selection=selection,
            root_item_id=root_item_id,
            cursor=cursor,
            path_scoped=path_scoped,
            capture_paths=capture_paths,
            root_id=root_id,
            rule_resolve_folder_ids=rule_resolve_folder_ids,
        )
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
        """Yield selected documents with the completed scan's total count.

        Args:
            drive_id: Graph drive to scan.
            selection: Include and exclude rules applied to file items.
            root_item_id: Root item to scan, defaulting to the drive root.

        Yields:
            Each selected document with total and available counts set to the final
            number of selected documents.
        """
        scan = await self.scan_metadata(
            drive_id,
            selection,
            root_item_id,
            None,
        )
        total = len(scan.documents)
        for document in scan.documents:
            yield DocumentMetadataItem(document, total, total)
