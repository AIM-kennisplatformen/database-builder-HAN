from collections.abc import AsyncIterator
from dataclasses import dataclass

from domain.exceptions.metadata_inventory_errors import (
    MetadataSourceError,
    SourceUnavailable,
)
from domain.models.documents.document import Document
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
    DocumentMetadataSource,
)


@dataclass(frozen=True)
class InventoryMetadataRequest:
    drive_id: str
    extensions: list[str]
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.drive_id.strip():
            raise ValueError("drive_id is required")
        normalized = [
            extension.strip().lower().removeprefix(".") for extension in self.extensions
        ]
        if not normalized or any(
            not extension.isascii() or not extension.isalnum()
            for extension in normalized
        ):
            raise ValueError("extensions must be nonempty and alphanumeric")
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be positive")
        object.__setattr__(self, "extensions", normalized)


@dataclass(frozen=True)
class InventoryMetadataProgress:
    document: Document


async def _stream_document_metadata_source(
    source: DocumentMetadataSource,
    drive_id: str,
    extensions: list[str],
    limit: int | None,
) -> AsyncIterator[DocumentMetadataItem]:
    try:
        async for item in source.stream_metadata(drive_id, extensions, limit):
            yield item
    except MetadataSourceError:
        raise
    except Exception as exc:
        raise SourceUnavailable("Document metadata source is unavailable") from exc


async def inventory_metadata(
    req: InventoryMetadataRequest,
    source: DocumentMetadataSource,
) -> AsyncIterator[InventoryMetadataProgress]:
    async for item in _stream_document_metadata_source(
        source, req.drive_id, req.extensions, req.limit
    ):
        yield InventoryMetadataProgress(document=item.document)
