from collections.abc import AsyncIterator
from datetime import datetime
from typing import cast, override
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient
from pydantic import BaseModel, Field

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
    async def scan_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str = "root",
        cursor: str | None = None,
    ) -> DocumentMetadataScan:
        base_url = f"https://graph.microsoft.com/v1.0/drives/{quote(drive_id, safe='')}"
        path_scoped = any(
            rule.path for rule in selection.include_rules + selection.exclude_rules
        )
        root_id: str | None = None
        rule_folder_ids: dict[str, str] = {}
        if path_scoped:
            root_id = (
                await self._folder_id(drive_id, "", root_item_id)
                if root_item_id == "root"
                else root_item_id
            )
            for path in {
                rule.path
                for rule in selection.include_rules + selection.exclude_rules
                if rule.path
            }:
                rule_folder_ids[path] = await self._folder_id(
                    drive_id, path, root_item_id
                )

        delta = (
            self.client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(root_item_id)
            .delta
        )
        delta_path = (
            "root"
            if root_item_id == "root"
            else f"items/{quote(root_item_id, safe='')}"
        )
        baseline_url = f"{base_url}/{delta_path}/delta"
        baseline = cursor is None or path_scoped
        next_url: str | None = baseline_url if baseline else cursor
        seen_urls: set[str] = set()
        matching_items: dict[str, GraphDriveItem] = {}
        folder_parents: dict[str, str | None] = {}
        deleted_ids: set[str] = set()
        delta_link: str | None = None

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
            try:
                raise_for_graph_error(
                    response, "Graph rejected the drive metadata request"
                )
            except MetadataSourceError as exc:
                if exc.status_code != 410 or baseline:
                    raise
                # An expired cursor must not publish a partial delta.
                baseline = True
                next_url = baseline_url
                seen_urls.clear()
                matching_items.clear()
                folder_parents.clear()
                deleted_ids.clear()
                continue

            page = GraphDeltaPage.model_validate_json(response.content)
            for item in page.value:
                if item.deleted is not None or item.folder is None:
                    _ = folder_parents.pop(item.id, None)
                else:
                    folder_parents[item.id] = (
                        item.parent_reference.id if item.parent_reference else None
                    )
                excluded = (
                    item.deleted is not None or item.file is None or not item.name
                )
                if item.deleted is not None or (excluded and not baseline):
                    deleted_ids.add(item.id)
                else:
                    deleted_ids.discard(item.id)
                if excluded:
                    _ = matching_items.pop(item.id, None)
                else:
                    matching_items[item.id] = item

            next_url = page.next_link
            if next_url is None and page.delta_link is None:
                raise ValueError("Graph did not complete the drive delta scan")
            if next_url is None:
                delta_link = page.delta_link

        def ancestor_ids(item: GraphDriveItem) -> set[str]:
            parent_id = item.parent_reference.id if item.parent_reference else None
            found: set[str] = set()
            seen: set[str] = set()
            while parent_id is not None:
                if parent_id in seen:
                    raise ValueError("Graph returned incomplete folder ancestry")
                seen.add(parent_id)
                found.add(parent_id)
                if parent_id == root_id:
                    break
                if parent_id not in folder_parents:
                    raise ValueError("Graph returned incomplete folder ancestry")
                parent_id = folder_parents[parent_id]
            if parent_id is None or (root_id is not None and root_id not in found):
                raise ValueError("Graph returned incomplete folder ancestry")
            return found

        def selected_by_rules(item: GraphDriveItem, extension: str) -> bool:
            ancestors: set[str] = ancestor_ids(item) if path_scoped else set()
            candidates: list[tuple[int, bool]] = []
            for rules, included in (
                (selection.include_rules, True),
                (selection.exclude_rules, False),
            ):
                for rule in rules:
                    rule_id = rule_folder_ids.get(rule.path, root_id)
                    if rule_id is not None and rule_id not in ancestors:
                        continue
                    if rule.extensions and extension not in rule.extensions:
                        continue
                    candidates.append(
                        (rule.path.count("/") + bool(rule.path), included)
                    )
            if not candidates:
                return not selection.include_rules
            specificity = max(depth for depth, _ in candidates)
            return next(
                included for depth, included in candidates if depth == specificity
            )

        selected: list[GraphDriveItem] = []
        for item in matching_items.values():
            if item.name is None:
                continue
            extension = item.name.rpartition(".")[2].lower() if "." in item.name else ""
            if selected_by_rules(item, extension):
                selected.append(item)
            elif not baseline:
                deleted_ids.add(item.id)
        if delta_link is None:
            raise ValueError("Graph did not provide a completed delta cursor")
        validate_graph_url(delta_link)
        return DocumentMetadataScan(
            documents=tuple(self._document(drive_id, item) for item in selected),
            deleted_item_ids=tuple(sorted(deleted_ids)),
            delta_link=delta_link,
            baseline=baseline,
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
