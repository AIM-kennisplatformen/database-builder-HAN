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
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)

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

        def normalize_paths(paths: list[str], field_name: str) -> list[str]:
            normalized_paths: list[str] = []
            for path in paths:
                normalized_path = path.strip("/")
                if not normalized_path or any(
                    not segment.strip() or segment in {".", ".."}
                    for segment in normalized_path.split("/")
                ):
                    raise ValueError(
                        f"{field_name} must contain folder paths relative to the drive root"
                    )
                if normalized_path not in normalized_paths:
                    normalized_paths.append(normalized_path)
            return normalized_paths

        object.__setattr__(
            self, "include_paths", normalize_paths(self.include_paths, "include_paths")
        )
        object.__setattr__(
            self, "exclude_paths", normalize_paths(self.exclude_paths, "exclude_paths")
        )
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
    include_paths: list[str],
    exclude_paths: list[str],
) -> AsyncIterator[DocumentMetadataItem]:
    try:
        async for item in source.stream_metadata(
            drive_id, extensions, include_paths, exclude_paths
        ):
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
        source, req.drive_id, req.extensions, req.include_paths, req.exclude_paths
    ):
        try:
            await store.save_document(item.document)
        except Exception as exc:
            raise StoreUnavailable("Document metadata store is unavailable") from exc
        yield InventoryMetadataProgress(
            document=item.document, total=item.total, available=item.available
        )
