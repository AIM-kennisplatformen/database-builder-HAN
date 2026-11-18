from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from json import dumps

from domain.exceptions.metadata_inventory_errors import (
    MetadataRunAlreadyActive,
    MetadataSourceError,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.documents.document import Document
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataItem,
    DocumentMetadataSource,
    DocumentRefreshSource,
)
from domain.ports.documents.document_store import DocumentRefreshStore, DocumentStore


@dataclass(frozen=True)
class InventoryMetadataRequest:
    drive_id: str
    extensions: list[str] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    exclude_extensions: list[str] = field(default_factory=list)
    root_item_id: str = "root"

    def __post_init__(self) -> None:
        if not self.drive_id.strip():
            raise ValueError("drive_id is required")
        if not self.root_item_id.strip():
            raise ValueError("root_item_id is required")

        def normalize_extensions(extensions: list[str]) -> list[str]:
            normalized = [
                extension.strip().lower().removeprefix(".") for extension in extensions
            ]
            if any(
                not extension.isascii() or not extension.isalnum()
                for extension in normalized
            ):
                raise ValueError("extensions must be alphanumeric")
            return normalized

        def normalize_paths(paths: list[str], field_name: str) -> list[str]:
            normalized_paths: list[str] = []
            for path in paths:
                normalized_path = path.strip("/")
                if not normalized_path or any(
                    not segment.strip() or segment in {".", ".."}
                    for segment in normalized_path.split("/")
                ):
                    raise ValueError(
                        f"{field_name} must contain folder paths relative to the scan root"
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
        object.__setattr__(self, "extensions", normalize_extensions(self.extensions))
        object.__setattr__(
            self, "exclude_extensions", normalize_extensions(self.exclude_extensions)
        )


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
    exclude_extensions: list[str],
    root_item_id: str,
) -> AsyncIterator[DocumentMetadataItem]:
    try:
        async for item in source.stream_metadata(
            drive_id,
            extensions,
            include_paths,
            exclude_paths,
            exclude_extensions,
            root_item_id,
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
        source,
        req.drive_id,
        req.extensions,
        req.include_paths,
        req.exclude_paths,
        req.exclude_extensions,
        req.root_item_id,
    ):
        try:
            await store.save_document(item.document)
        except Exception as exc:
            raise StoreUnavailable("Document metadata store is unavailable") from exc
        yield InventoryMetadataProgress(
            document=item.document, total=item.total, available=item.available
        )


async def refresh_metadata(
    principal_id: str,
    req: InventoryMetadataRequest,
    source: DocumentRefreshSource,
    store: DocumentRefreshStore,
) -> AsyncIterator[InventoryMetadataProgress]:
    """Stage a Graph delta and publish its metadata and cursor atomically."""
    selection = dumps(
        {
            "root_item_id": req.root_item_id,
            "extensions": sorted(set(req.extensions)),
            "include_paths": sorted(set(req.include_paths)),
            "exclude_paths": sorted(set(req.exclude_paths)),
            "exclude_extensions": sorted(set(req.exclude_extensions)),
        },
        sort_keys=True,
    )
    try:
        run = await store.begin_metadata_run(principal_id, req.drive_id, selection)
    except MetadataRunAlreadyActive:
        raise
    except Exception as exc:
        raise StoreUnavailable("Document metadata store is unavailable") from exc
    try:
        try:
            scan = await source.scan_metadata(
                req.drive_id,
                req.extensions,
                req.include_paths,
                req.exclude_paths,
                req.exclude_extensions,
                req.root_item_id,
                run.cursor if not (req.include_paths or req.exclude_paths) else None,
            )
        except MetadataSourceError:
            raise
        except Exception as exc:
            raise SourceUnavailable("Document metadata source is unavailable") from exc
        total = len(scan.documents)
        for document in scan.documents:
            try:
                await store.stage_document(run.id, document)
            except Exception as exc:
                raise StoreUnavailable(
                    "Document metadata store is unavailable"
                ) from exc
            yield InventoryMetadataProgress(document, total, total)
        for item_id in scan.deleted_item_ids:
            try:
                await store.stage_deleted(run.id, item_id)
            except Exception as exc:
                raise StoreUnavailable(
                    "Document metadata store is unavailable"
                ) from exc
        try:
            await store.publish_metadata_run(
                run.id, scan.delta_link, scan.baseline, run.same_selection
            )
        except Exception as exc:
            raise StoreUnavailable("Document metadata store is unavailable") from exc
    except BaseException:
        await store.fail_metadata_run(run.id)
        raise
