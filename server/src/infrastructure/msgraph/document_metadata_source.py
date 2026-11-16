from collections.abc import AsyncIterator
from typing import cast, override
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient
from pydantic import BaseModel, Field

from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileHashes,
    FileMetadata,
)
from domain.ports.sources.document_metadata_source import DocumentMetadataSource
from infrastructure.msgraph.base import raise_for_graph_error, validate_graph_url


class GraphFileHashes(BaseModel):
    sha1: str | None = Field(default=None, alias="sha1Hash")
    sha256: str | None = Field(default=None, alias="sha256Hash")
    quick_xor: str | None = Field(default=None, alias="quickXorHash")


class GraphFileFacet(BaseModel):
    mime_type: str | None = Field(default=None, alias="mimeType")
    hashes: GraphFileHashes | None = None


class GraphDriveItem(BaseModel):
    id: str
    name: str | None = None
    file: GraphFileFacet | None = None
    deleted: dict[str, object] | None = None
    c_tag: str | None = Field(default=None, alias="cTag")
    web_url: str | None = Field(default=None, alias="webUrl")
    size: int | None = None


class GraphDeltaPage(BaseModel):
    value: list[GraphDriveItem]
    next_link: str | None = Field(default=None, alias="@odata.nextLink")
    delta_link: str | None = Field(default=None, alias="@odata.deltaLink")


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
            document_metadata=DocumentMetadata(),
        )

    @override
    async def stream_metadata(
        self, drive_id: str, extensions: list[str], limit: int | None
    ) -> AsyncIterator[tuple[Document, int, int]]:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")

        delta = (
            self.client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id("root")
            .delta
        )
        next_url: str | None = (
            "https://graph.microsoft.com/v1.0/drives/"
            f"{quote(drive_id, safe='')}/root/delta"
        )
        seen_urls: set[str] = set()
        processed = 0

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
                if item.deleted is not None or item.file is None or not item.name:
                    continue
                if item.name.rpartition(".")[2].lower() not in extensions:
                    continue
                processed += 1
                yield self._document(drive_id, item), processed, processed
                if limit is not None and processed >= limit:
                    return

            next_url = page.next_link
            if next_url is None and page.delta_link is None:
                raise ValueError("Graph did not complete the drive delta scan")
