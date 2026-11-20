import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from uuid import UUID

import anyio

from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataRunAlreadyActive,
    MetadataSourceError,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.documents.document import Document
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataScan,
    DocumentRefreshSource,
)
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    MetadataIngestRun,
    MetadataRunPublication,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetadataIngestDriveRequest:
    drive_id: str
    extensions: list[str] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    exclude_extensions: list[str] = field(default_factory=list)
    root_item_id: str = "root"

    def __post_init__(self) -> None:
        if not self.drive_id.strip():
            raise InvalidMetadataSelection("drive_id is required")
        if not self.root_item_id.strip():
            raise InvalidMetadataSelection("root_item_id is required")

        def normalize_extensions(extensions: list[str]) -> list[str]:
            normalized = [
                extension.strip().lower().removeprefix(".") for extension in extensions
            ]
            if any(
                not extension.isascii() or not extension.isalnum()
                for extension in normalized
            ):
                raise InvalidMetadataSelection("extensions must be alphanumeric")
            return normalized

        def normalize_paths(paths: list[str], field_name: str) -> list[str]:
            normalized_paths: list[str] = []
            for path in paths:
                normalized_path = path.strip("/")
                if not normalized_path or any(
                    not segment.strip() or segment in {".", ".."}
                    for segment in normalized_path.split("/")
                ):
                    raise InvalidMetadataSelection(
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
class MetadataIngestDriveProgress:
    document: Document
    total: int
    available: int


@dataclass(frozen=True)
class MetadataIngestPrepared:
    publication: MetadataRunPublication


async def _begin_metadata_run(
    store: DocumentRefreshStore,
    principal_id: str,
    req: MetadataIngestDriveRequest,
) -> MetadataIngestRun:
    selection = MetadataSelection(
        root_item_id=req.root_item_id,
        extensions=tuple(sorted(set(req.extensions))),
        include_paths=tuple(sorted(set(req.include_paths))),
        exclude_paths=tuple(sorted(set(req.exclude_paths))),
        exclude_extensions=tuple(sorted(set(req.exclude_extensions))),
    )
    try:
        return await store.begin_metadata_run(principal_id, req.drive_id, selection)
    except MetadataRunAlreadyActive:
        raise
    except Exception as exc:
        raise StoreUnavailable("Document metadata store is unavailable") from exc


async def _scan_metadata_source(
    source: DocumentRefreshSource,
    req: MetadataIngestDriveRequest,
    cursor: str | None,
) -> DocumentMetadataScan:
    try:
        return await source.scan_metadata(
            req.drive_id,
            req.extensions,
            req.include_paths,
            req.exclude_paths,
            req.exclude_extensions,
            req.root_item_id,
            cursor if not (req.include_paths or req.exclude_paths) else None,
        )
    except MetadataSourceError:
        raise
    except Exception as exc:
        raise SourceUnavailable("Document metadata source is unavailable") from exc


async def _stage_document(
    store: DocumentRefreshStore, run_id: UUID, document: Document
) -> None:
    try:
        await store.stage_document(run_id, document)
    except Exception as exc:
        raise StoreUnavailable("Document metadata store is unavailable") from exc


async def _stage_deleted_item(
    store: DocumentRefreshStore, run_id: UUID, item_id: str
) -> None:
    try:
        await store.stage_deleted_item(run_id, item_id)
    except Exception as exc:
        raise StoreUnavailable("Document metadata store is unavailable") from exc


async def publish_metadata_runs(
    store: DocumentRefreshStore,
    publications: tuple[MetadataRunPublication, ...],
) -> None:
    try:
        await store.publish_metadata_runs(publications)
    except Exception as exc:
        raise StoreUnavailable("Document metadata store is unavailable") from exc


async def _publish_metadata_run(
    store: DocumentRefreshStore,
    publication: MetadataRunPublication,
) -> None:
    try:
        await store.publish_metadata_run(
            publication.run_id,
            publication.delta_link,
            publication.baseline,
            publication.reconcile,
        )
    except Exception as exc:
        raise StoreUnavailable("Document metadata store is unavailable") from exc


async def _stage_metadata(
    store: DocumentRefreshStore,
    run_id: UUID,
    scan: DocumentMetadataScan,
) -> AsyncGenerator[MetadataIngestDriveProgress]:
    """Stage a completed scan and stream progress as each document is staged."""
    total = len(scan.documents)
    for document in scan.documents:
        await _stage_document(store, run_id, document)
        yield MetadataIngestDriveProgress(document, total, total)
    for item_id in scan.deleted_item_ids:
        await _stage_deleted_item(store, run_id, item_id)


async def _discard_metadata_run(
    store: DocumentRefreshStore, run_id: UUID
) -> None:
    """Clear staging; log cleanup failures so they do not mask the scan failure."""
    with anyio.CancelScope(shield=True):
        try:
            await store.discard_metadata_run(run_id)
        except KeyboardInterrupt, SystemExit:
            raise
        except BaseException:
            # Startup recovery will clear staging left behind by an unsuccessful cleanup.
            logger.exception("Could not clear staging for metadata run %s", run_id)


async def discard_metadata_runs(
    store: DocumentRefreshStore, run_ids: tuple[UUID, ...]
) -> None:
    """Clear every staged drive in one cancelled or failed group request."""
    with anyio.CancelScope(shield=True):
        for run_id in run_ids:
            await _discard_metadata_run(store, run_id)


async def prepare_metadata(
    principal_id: str,
    req: MetadataIngestDriveRequest,
    source: DocumentRefreshSource,
    store: DocumentRefreshStore,
) -> AsyncGenerator[MetadataIngestDriveProgress | MetadataIngestPrepared]:
    """Scan and stage one drive without publishing it."""
    run: MetadataIngestRun | None = None
    with anyio.CancelScope(shield=True):
        run = await _begin_metadata_run(store, principal_id, req)
    assert run is not None
    try:
        scan = await _scan_metadata_source(source, req, run.cursor)
        updates = _stage_metadata(store, run.id, scan)
        try:
            async for update in updates:
                yield update
        finally:
            with anyio.CancelScope(shield=True):
                await updates.aclose()
        yield MetadataIngestPrepared(
            MetadataRunPublication(
                run.id, scan.delta_link, scan.baseline, run.same_selection
            )
        )
    except BaseException:
        await _discard_metadata_run(store, run.id)
        raise


async def ingest_metadata(
    principal_id: str,
    req: MetadataIngestDriveRequest,
    source: DocumentRefreshSource,
    store: DocumentRefreshStore,
) -> AsyncGenerator[MetadataIngestDriveProgress]:
    """Scan, stage, then publish one drive's metadata and cursor atomically."""
    publication: MetadataRunPublication | None = None
    try:
        updates = prepare_metadata(principal_id, req, source, store)
        try:
            async for update in updates:
                if isinstance(update, MetadataIngestPrepared):
                    publication = update.publication
                else:
                    yield update
        finally:
            with anyio.CancelScope(shield=True):
                await updates.aclose()
        if publication is None:
            raise RuntimeError("Metadata preparation ended without a run")
        await _publish_metadata_run(store, publication)
    except BaseException:
        if publication is not None:
            await discard_metadata_runs(store, (publication.run_id,))
        raise
