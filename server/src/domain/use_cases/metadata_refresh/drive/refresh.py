import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from uuid import UUID

import anyio

from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataRunAlreadyActive,
    MetadataSourceError,
    MetadataSourceUnavailable,
    MetadataStoreUnavailable,
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
    MetadataDriveIngestRun,
    MetadataDriveRunPublication,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetadataDriveIngestRequest:
    """Configure a drive scan, including normalized include and exclude rules.

    Construction rejects blank drive or root IDs and normalizes selector rules.
    """

    drive_id: str
    include_rules: tuple[MetadataPathRule, ...] = ()
    exclude_rules: tuple[MetadataPathRule, ...] = ()
    root_item_id: str = "root"
    operation_id: UUID | None = None

    def __post_init__(self) -> None:
        """Validate required IDs and normalize selection rules."""
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
class MetadataDriveIngestProgress:
    """Report one staged document and the source counts for its scan."""

    document: Document
    total: int
    available: int


@dataclass(frozen=True)
class MetadataDriveIngestPrepared:
    """Carry a staged drive publication for a caller to commit later."""

    publication: MetadataDriveRunPublication


async def _begin_metadata_run(
    store: DocumentRefreshStore,
    principal_id: str,
    req: MetadataDriveIngestRequest,
) -> MetadataDriveIngestRun:
    """Create a run, preserving active-run conflicts and mapping store failures."""
    selection = MetadataSelection(req.include_rules, req.exclude_rules)
    try:
        return await store.begin_metadata_run(
            principal_id, req.drive_id, selection, req.operation_id
        )
    except MetadataRunAlreadyActive:
        raise
    except Exception as exc:
        raise MetadataStoreUnavailable("Document metadata store is unavailable") from exc


async def _scan_metadata_source(
    source: DocumentRefreshSource,
    req: MetadataDriveIngestRequest,
    cursor: str | None,
    capture_relative_paths: bool = False,
) -> DocumentMetadataScan:
    """Scan selected metadata, disabling cursor use when paths must be captured.

    Path selectors require a full scan because a cursor does not include unchanged
    items needed to evaluate paths. Source status errors are preserved for callers.
    """
    try:
        selection = MetadataSelection(req.include_rules, req.exclude_rules)
        capture_paths = capture_relative_paths or any(
            rule.path for rule in selection.include_rules + selection.exclude_rules
        )
        scan_cursor = cursor if not capture_paths else None
        if capture_relative_paths:
            return await source.scan_metadata(
                req.drive_id,
                selection,
                req.root_item_id,
                scan_cursor,
                capture_relative_paths=True,
            )
        return await source.scan_metadata(
            req.drive_id,
            selection,
            req.root_item_id,
            scan_cursor,
        )
    except MetadataSourceError:
        raise
    except Exception as exc:
        raise MetadataSourceUnavailable("Document metadata source is unavailable") from exc


async def _stage_document(
    store: DocumentRefreshStore, run_id: UUID, document: Document
) -> None:
    try:
        await store.stage_document(run_id, document)
    except Exception as exc:
        raise MetadataStoreUnavailable("Document metadata store is unavailable") from exc


async def _stage_deleted_item(
    store: DocumentRefreshStore, run_id: UUID, item_id: str
) -> None:
    try:
        await store.stage_deleted_item(run_id, item_id)
    except Exception as exc:
        raise MetadataStoreUnavailable("Document metadata store is unavailable") from exc


async def publish_metadata_runs(
    store: DocumentRefreshStore,
    publications: tuple[MetadataDriveRunPublication, ...],
) -> None:
    """Publish prepared drive runs together so readers see one consistent update.

    Args:
        store: Metadata store that owns the staged runs.
        publications: Prepared runs and cursors to publish in one transaction.

    Raises:
        MetadataStoreUnavailable: If publication fails.
    """
    try:
        await store.publish_metadata_runs(publications)
    except Exception as exc:
        raise MetadataStoreUnavailable("Document metadata store is unavailable") from exc


async def _publish_metadata_run(
    store: DocumentRefreshStore,
    publication: MetadataDriveRunPublication,
) -> None:
    try:
        await store.publish_metadata_run(
            publication.run_id,
            publication.delta_link,
            publication.baseline,
            publication.reconcile,
        )
    except Exception as exc:
        raise MetadataStoreUnavailable("Document metadata store is unavailable") from exc


async def _stage_metadata(
    store: DocumentRefreshStore,
    run_id: UUID,
    scan: DocumentMetadataScan,
) -> AsyncGenerator[MetadataDriveIngestProgress]:
    """Stage a completed scan and stream progress as each document is staged."""
    total = len(scan.documents)
    for document in scan.documents:
        await _stage_document(store, run_id, document)
        yield MetadataDriveIngestProgress(document, total, total)
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
    """Clear staged runs after a failed or cancelled multi-drive request.

    Args:
        store: Metadata store that owns the staged runs.
        run_ids: Staged run identifiers to discard.
    """
    with anyio.CancelScope(shield=True):
        for run_id in run_ids:
            await _discard_metadata_run(store, run_id)


async def stage_metadata_refresh(
    principal_id: str,
    req: MetadataDriveIngestRequest,
    source: DocumentRefreshSource,
    store: DocumentRefreshStore,
    *,
    capture_relative_paths: bool = False,
) -> AsyncGenerator[MetadataDriveIngestProgress | MetadataDriveIngestPrepared]:
    """Scan and stage one drive without making its metadata visible.

    The caller must publish the prepared token; closing or failing this generator
    discards the staged run.

    Args:
        principal_id: Principal associated with the metadata run.
        req: Drive ID, root item, and include/exclude rules.
        source: Source used to scan drive metadata.
        store: Store used to stage documents and deletions.
        capture_relative_paths: Include relative paths in the source scan when
            downstream selection rules need them.

    Yields:
        Progress for each staged document, followed by a publication token.

    Raises:
        MetadataRunAlreadyActive: If this principal already has an active run
            for the drive.
        MetadataSourceError: If the source rejects the scan with a known status.
        MetadataSourceUnavailable: If the source fails for an unexpected reason.
        MetadataStoreUnavailable: If a staging operation fails.
    """
    run: MetadataDriveIngestRun | None = None
    with anyio.CancelScope(shield=True):
        run = await _begin_metadata_run(store, principal_id, req)
    assert run is not None
    try:
        scan = await _scan_metadata_source(
            source, req, run.cursor, capture_relative_paths
        )
        staged_document_events = _stage_metadata(store, run.id, scan)
        try:
            async for document_event in staged_document_events:
                yield document_event
        finally:
            with anyio.CancelScope(shield=True):
                await staged_document_events.aclose()
        yield MetadataDriveIngestPrepared(
            MetadataDriveRunPublication(
                run.id, scan.delta_link, scan.baseline, run.same_selection
            )
        )
    except BaseException:
        await _discard_metadata_run(store, run.id)
        raise


async def refresh_drive_metadata_atomically(
    principal_id: str,
    req: MetadataDriveIngestRequest,
    source: DocumentRefreshSource,
    store: DocumentRefreshStore,
) -> AsyncGenerator[MetadataDriveIngestProgress]:
    """Scan, stage, and publish one drive's metadata and cursor atomically.

    If the operation fails after preparation, its staged run is discarded before
    the error is propagated.

    Args:
        principal_id: Principal associated with the metadata run.
        req: Drive ID, root item, and include/exclude rules.
        source: Source used to scan drive metadata.
        store: Store used to stage and publish documents.

    Yields:
        Progress for each document staged from the source.

    Raises:
        MetadataRunAlreadyActive: If another refresh is active for the drive.
        MetadataSourceError: If the source rejects the scan with a known status.
        MetadataSourceUnavailable: If the source fails for an unexpected reason.
        MetadataStoreUnavailable: If staging or publication fails.
    """
    publication: MetadataDriveRunPublication | None = None
    try:
        preparation_events = stage_metadata_refresh(principal_id, req, source, store)
        try:
            async for preparation_event in preparation_events:
                match preparation_event:
                    case MetadataDriveIngestPrepared(publication=prepared_publication):
                        publication = prepared_publication
                    case MetadataDriveIngestProgress():
                        yield preparation_event
        finally:
            with anyio.CancelScope(shield=True):
                await preparation_events.aclose()
        if publication is None:
            raise RuntimeError("Metadata preparation ended without a run")
        await _publish_metadata_run(store, publication)
    except BaseException:
        if publication is not None:
            await discard_metadata_runs(store, (publication.run_id,))
        raise
