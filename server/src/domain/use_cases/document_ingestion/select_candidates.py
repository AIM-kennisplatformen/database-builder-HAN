"""Refresh metadata and select eligible document candidates."""

import logging
from collections.abc import AsyncGenerator
from uuid import UUID

import anyio

from domain.models.documents.document_ingestion import (
    DocumentIngestRequest,
    select_ingest_file,
)
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import CombinedDocumentRefreshStore
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.document_ingestion._models import (
    DocumentCandidateSelectionFailure,
)
from domain.use_cases.document_ingestion._selectors import (
    build_metadata_refresh_request,
    select_path_capture_drive_ids,
)
from domain.use_cases.metadata_refresh.group._models import (
    MetadataDriveFilesSelected,
    MetadataDrivePublished,
    MetadataDriveRefreshFailure,
    MetadataGroupIngestProgress,
    MetadataGroupIngestStarted,
    MetadataRefreshRunResult,
)
from domain.use_cases.metadata_refresh.group.per_drive import (
    refresh_group_metadata_per_drive,
)

logger = logging.getLogger(__name__)


async def _select_published_drive(
    user_id: str,
    request: DocumentIngestRequest,
    store: CombinedDocumentRefreshStore,
    operation_id: UUID,
    event: MetadataDrivePublished,
) -> MetadataDriveFilesSelected:
    """Query candidates visible in the published run and record their selection."""
    candidate_documents = await store.query_visible_document_candidates(
        user_id, event.drive_id, event.metadata_run_id
    )
    candidate_selections = tuple(
        selection
        for candidate_document in candidate_documents
        if (selection := select_ingest_file(candidate_document, request)) is not None
    )
    await store.record_document_ingest_selections(operation_id, candidate_selections)
    return MetadataDriveFilesSelected(
        event.drive_id, event.metadata_run_id, len(candidate_selections)
    )


async def _record_selection_failure(
    exc: BaseException,
    operation_id: UUID,
    store: CombinedDocumentRefreshStore,
    metadata_refresh_events: AsyncGenerator[object],
) -> None:
    """Persist candidate selection failures and preserve cancellation status."""
    cancelled = isinstance(exc, anyio.get_cancelled_exc_class() | GeneratorExit)
    if not cancelled and isinstance(exc, Exception):
        logger.exception("Document candidate selection failed")
    if cancelled:
        with anyio.CancelScope(shield=True):
            await store.fail_group_operation(
                operation_id,
                "request_interrupted",
                interrupted=True,
                summary={"failure_stage": "document_ingest.selection"},
            )
        return
    try:
        _ = await metadata_refresh_events.athrow(DocumentCandidateSelectionFailure())
    except DocumentCandidateSelectionFailure:
        return


async def refresh_and_select_group_document_candidates(
    user_id: str,
    request: DocumentIngestRequest,
    drive_source: GroupDriveSource,
    metadata_source: DocumentRefreshSource,
    store: CombinedDocumentRefreshStore,
) -> AsyncGenerator[
    MetadataGroupIngestStarted
    | MetadataGroupIngestProgress
    | MetadataDrivePublished
    | MetadataDriveFilesSelected
    | MetadataDriveRefreshFailure
    | MetadataRefreshRunResult
]:
    """Refresh metadata, then persist eligible candidates per published drive.

    Args:
        user_id: User whose group drives and visible documents are accessed.
        request: Group and document-selection criteria.
        drive_source: Source for the user's group drive locations.
        metadata_source: Source used to refresh drive metadata.
        store: Store for metadata refresh and candidate selections.

    Yields:
        Metadata refresh events, per-drive publication and selection events, and
        the final refresh result.

    """
    metadata_request = build_metadata_refresh_request(request)
    metadata_refresh_events = refresh_group_metadata_per_drive(
        user_id,
        metadata_request,
        drive_source,
        metadata_source,
        store,
        validation_include_drive_ids=frozenset(
            selector.drive_id for selector in request.include
        ),
        validation_exclude_drive_ids=frozenset(
            selector.drive_id for selector in request.exclude
        ),
        capture_relative_paths_drive_ids=select_path_capture_drive_ids(request),
    )
    operation_id: UUID | None = None
    try:
        async for event in metadata_refresh_events:
            match event:
                case MetadataGroupIngestStarted(operation_id=started_operation_id):
                    operation_id = started_operation_id
                case MetadataDrivePublished():
                    yield event
                    if operation_id is None:
                        raise RuntimeError(
                            "Metadata refresh started without an operation"
                        )
                    yield await _select_published_drive(
                        user_id, request, store, operation_id, event
                    )
                case _:
                    yield event
    except BaseException as exc:
        if operation_id is not None:
            await _record_selection_failure(
                exc, operation_id, store, metadata_refresh_events
            )
        raise
    finally:
        await metadata_refresh_events.aclose()
