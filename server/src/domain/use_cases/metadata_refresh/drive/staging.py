"""Adapt per-drive metadata preparation to group progress and skip events."""

from collections.abc import AsyncGenerator

import anyio

from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import DocumentRefreshStore
from domain.use_cases.metadata_refresh.drive.refresh import (
    MetadataIngestDriveProgress,
    MetadataIngestDriveRequest,
    MetadataIngestPrepared,
    stage_metadata_refresh,
)
from domain.use_cases.metadata_refresh.group._models import (
    MetadataIngestDriveFailure,
    MetadataIngestProgress,
    MetadataIngestSkipped,
)


async def stage_drive_metadata_refresh(
    user_id: str,
    request: MetadataIngestDriveRequest,
    metadata_source: DocumentRefreshSource,
    store: DocumentRefreshStore,
    *,
    allow_skip: bool,
    capture_relative_paths: bool = False,
) -> AsyncGenerator[
    MetadataIngestProgress | MetadataIngestSkipped | MetadataIngestPrepared
]:
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
                    case MetadataIngestPrepared():
                        yield preparation_event
                    case MetadataIngestDriveProgress():
                        yield MetadataIngestProgress(
                            request.drive_id, preparation_event
                        )
        except MetadataSourceError as exc:
            if exc.status_code == 403 and allow_skip:
                yield MetadataIngestSkipped(request.drive_id, str(exc))
                return
            raise MetadataIngestDriveFailure(request.drive_id, exc) from exc
        except Exception as exc:
            raise MetadataIngestDriveFailure(request.drive_id, exc) from exc
    finally:
        with anyio.CancelScope(shield=True):
            await preparation_events.aclose()
