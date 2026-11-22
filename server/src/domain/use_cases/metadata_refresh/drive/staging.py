"""Adapt per-drive metadata preparation to group progress and skip events."""

from collections.abc import AsyncGenerator

import anyio

from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import DocumentRefreshStore
from domain.use_cases.metadata_refresh.drive.refresh import (
    MetadataDriveIngestPrepared,
    MetadataDriveIngestProgress,
    MetadataDriveIngestRequest,
    stage_metadata_refresh,
)
from domain.use_cases.metadata_refresh.group._models import (
    MetadataDriveIngestFailure,
    MetadataGroupIngestProgress,
    MetadataGroupIngestSkipped,
)


async def stage_drive_metadata_refresh(
    user_id: str,
    request: MetadataDriveIngestRequest,
    metadata_source: DocumentRefreshSource,
    store: DocumentRefreshStore,
    *,
    allow_skip: bool,
    capture_relative_paths: bool = False,
) -> AsyncGenerator[
    MetadataGroupIngestProgress | MetadataGroupIngestSkipped | MetadataDriveIngestPrepared
]:
    """Adapt one drive's staging events for group refresh consumers.

    A 403 source error becomes a skipped event only when ``allow_skip`` is true;
    every other drive failure is wrapped with its drive ID. Closing this stream
    closes the underlying preparation stream, which discards uncommitted staging.

    Args:
        user_id: Principal whose metadata run is created.
        request: Drive ID, root, and path selection for the scan.
        metadata_source: Source used to scan drive metadata.
        store: Store used to stage the scan.
        allow_skip: Whether an inaccessible drive may be reported as skipped.
        capture_relative_paths: Whether source scans must include relative paths.

    Yields:
        Group progress and skip events, followed by the prepared publication.
    """
    preparation_events = stage_metadata_refresh(
        user_id,
        request,
        metadata_source,
        store,
        capture_relative_paths=capture_relative_paths,
    )
    try:
        try:
            async for preparation_event in preparation_events:
                match preparation_event:
                    case MetadataDriveIngestPrepared():
                        yield preparation_event
                    case MetadataDriveIngestProgress():
                        yield MetadataGroupIngestProgress(
                            request.drive_id, preparation_event
                        )
        except MetadataSourceError as exc:
            if exc.status_code == 403 and allow_skip:
                yield MetadataGroupIngestSkipped(request.drive_id, str(exc))
                return
            raise MetadataDriveIngestFailure(request.drive_id, exc) from exc
        except Exception as exc:
            raise MetadataDriveIngestFailure(request.drive_id, exc) from exc
    finally:
        with anyio.CancelScope(shield=True):
            await preparation_events.aclose()
