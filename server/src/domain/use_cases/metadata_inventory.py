from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from domain.exceptions.metadata_inventory_errors import (
    MetadataSourceError,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.documents.document import Document
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
    DocumentMetadataSource,
)
from domain.ports.documents.document_store import DocumentStore


@dataclass(frozen=True)
class InventoryMetadataRequest:
    drive_id: str
    extensions: list[str] = field(default_factory=list)
    limit: int | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        if not self.drive_id.strip():
            raise ValueError("drive_id is required")
        normalized = [
            extension.strip().lower().removeprefix(".") for extension in self.extensions
        ]
        if any(
            not extension.isascii() or not extension.isalnum()
            for extension in normalized
        ):
            raise ValueError("extensions must be alphanumeric")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be positive")
        if self.path is not None:
            path = self.path.strip("/")
            if path and any(
                not segment.strip() or segment in {".", ".."}
                for segment in path.split("/")
            ):
                raise ValueError(
                    "path must be a folder path relative to the drive root"
                )
            object.__setattr__(self, "path", path or None)
        object.__setattr__(self, "extensions", normalized)


@dataclass(frozen=True)
class InventoryMetadataProgress:
    document: Document
    total: int
    available: int


async def _stream_document_metadata_source(
    source: DocumentMetadataSource,
    drive_id: str,
    extensions: list[str],
    limit: int | None,
    path: str | None,
) -> AsyncIterator[DocumentMetadataItem]:
    try:
        async for item in source.stream_metadata(drive_id, extensions, limit, path):
            yield item
    except MetadataSourceError:
        raise
    except Exception as exc:
        raise SourceUnavailable("Document metadata source is unavailable") from exc


async def inventory_metadata(
    req: InventoryMetadataRequest,
    source: DocumentMetadataSource,
    store: DocumentStore,
) -> AsyncIterator[InventoryMetadataProgress]:
    async for item in _stream_document_metadata_source(
        source, req.drive_id, req.extensions, req.limit, req.path
    ):
        try:
            await store.save_document(item.document)
        except Exception as exc:
            raise StoreUnavailable("Document metadata store is unavailable") from exc
        yield InventoryMetadataProgress(
            document=item.document, total=item.total, available=item.available
        )
