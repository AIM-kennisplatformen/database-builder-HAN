from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from domain.models.documents.document import Document
from domain.models.documents.metadata_selection import MetadataSelection


@dataclass(frozen=True)
class DocumentMetadataItem:
    document: Document
    total: int
    available: int


@dataclass(frozen=True)
class DocumentMetadataScan:
    documents: tuple[Document, ...]
    deleted_item_ids: tuple[str, ...]
    delta_link: str
    baseline: bool


class DocumentMetadataSource(Protocol):
    def stream_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str,
    ) -> AsyncIterator[DocumentMetadataItem]: ...


class DocumentRefreshSource(Protocol):
    async def scan_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str,
        cursor: str | None,
    ) -> DocumentMetadataScan: ...
