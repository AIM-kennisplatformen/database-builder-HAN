import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
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
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    MetadataSelection,
    normalize_metadata_rules,
)
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
    include_rules: tuple[MetadataPathRule, ...] = ()
    exclude_rules: tuple[MetadataPathRule, ...] = ()
    root_item_id: str = "root"
    operation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.drive_id.strip():
            raise InvalidMetadataSelection("drive_id is required")
        if not self.root_item_id.strip():
            raise InvalidMetadataSelection("root_item_id is required")

        object.__setattr__(
            self,
            "include_rules",
            normalize_metadata_rules(self.include_rules, "include_rules"),
        )
        object.__setattr__(
            self,
            "exclude_rules",
            normalize_metadata_rules(self.exclude_rules, "exclude_rules"),
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
    selection = MetadataSelection(req.include_rules, req.exclude_rules)
    try:
        return await store.begin_metadata_run(
            principal_id, req.drive_id, selection, req.operation_id
        )
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
        selection = MetadataSelection(req.include_rules, req.exclude_rules)
        return await source.scan_metadata(
            req.drive_id,
            selection,
            req.root_item_id,
            cursor
            if not any(
                rule.path for rule in selection.include_rules + selection.exclude_rules
            )
            else None,
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


async def _discard_metadata_run(store: DocumentRefreshStore, run_id: UUID) -> None:
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
