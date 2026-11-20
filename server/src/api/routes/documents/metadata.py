from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import Annotated, ClassVar

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from api.errors import (
    PublicError,
    internal_error,
    log_api_failure,
    log_missing_outcome,
)
from api.progress import ProgressEventLimiter
from api.routes.documents.error_mapping import MetadataErrorMapper
from api.stream import StreamEvent, StreamEventKind, StreamLifecycle
from config import config
from dependencies import (
    get_current_graph_user_id,
    get_document_metadata_deleter,
    get_document_metadata_source,
    get_document_store,
    get_group_drive_source,
    require_metadata_delete_enabled,
)
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import (
    DocumentMetadataDeleter,
    DocumentRefreshStore,
)
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.metadata_ingest import (
    DriveMetadataSelector,
    MetadataIngestDriveFailure,
    MetadataIngestProgress,
    MetadataIngestRequest,
    MetadataIngestResult,
    MetadataIngestSkipped,
    MetadataIngestStarted,
    ingest_group_metadata,
)

router = APIRouter()


@router.delete(
    path="/metadata",
    summary="Delete all stored document metadata",
    include_in_schema=config.delete_endpoints.metadata,
)
async def delete_document_metadata(
    _delete_enabled: Annotated[None, Depends(require_metadata_delete_enabled)],
    _principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    store: Annotated[DocumentMetadataDeleter, Depends(get_document_metadata_deleter)],
) -> dict[str, bool]:
    if not await store.delete_all_metadata():
        raise HTTPException(
            status_code=409, detail="Document metadata cannot be reset during ingestion"
        )
    return {"deleted": True}


class DriveMetadataSelectorBody(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    drive_id: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)


class MetadataIngestRequestBody(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    include: list[DriveMetadataSelectorBody] = Field(
        default_factory=list,
        description="Drive, folder, and file-type selectors; empty selects every drive",
    )
    exclude: list[DriveMetadataSelectorBody] = Field(
        default_factory=list,
        description="Drive, folder, and file-type exclusions",
    )


class MetadataIngestEventStatus(StrEnum):
    PROGRESS = "progress"
    SKIPPED = "skipped"
    COMPLETE = "complete"
    ERROR = "error"


class MetadataIngestEvent(BaseModel):
    status: MetadataIngestEventStatus
    group_id: str
    processed: int
    drive_id: str | None = None
    total: int | None = None
    available: int | None = None
    item_id: str | None = None
    name: str | None = None
    error_status: int | None = None
    skipped_drive_ids: list[str] | None = None
    detail: str | None = None
    operation_id: str | None = None


def _event(
    update: MetadataIngestStarted
    | MetadataIngestProgress
    | MetadataIngestSkipped
    | MetadataIngestResult,
    group_id: str,
    operation_id: str | None,
) -> StreamEvent:
    if isinstance(update, MetadataIngestStarted):
        return StreamEvent(
            "started",
            {
                "status": "running",
                "group_id": group_id,
                "operation_id": str(update.operation_id),
            },
            StreamEventKind.NON_TERMINAL,
        )
    if isinstance(update, MetadataIngestResult):
        event = MetadataIngestEvent(
            status=MetadataIngestEventStatus.COMPLETE,
            group_id=group_id,
            processed=update.processed,
            skipped_drive_ids=list(update.skipped_drive_ids),
            operation_id=operation_id,
        )
        return StreamEvent(
            event.status.value,
            event.model_dump(exclude_none=True),
            StreamEventKind.COMPLETE,
        )
    if isinstance(update, MetadataIngestSkipped):
        event = MetadataIngestEvent(
            status=MetadataIngestEventStatus.SKIPPED,
            group_id=group_id,
            processed=update.processed,
            drive_id=update.drive_id,
            error_status=403,
            detail="Drive is not accessible",
            operation_id=operation_id,
        )
        return StreamEvent(
            event.status.value,
            event.model_dump(exclude_none=True),
            StreamEventKind.NON_TERMINAL,
        )

    progress = update.update
    event = MetadataIngestEvent(
        status=MetadataIngestEventStatus.PROGRESS,
        group_id=group_id,
        processed=update.processed,
        drive_id=update.drive_id,
        total=progress.total,
        available=progress.available,
        item_id=progress.document.id.id[1],
        name=progress.document.file_metadata.name,
        operation_id=operation_id,
    )
    return StreamEvent(
        event.status.value,
        event.model_dump(exclude_none=True),
        StreamEventKind.PROGRESS,
    )


def _failure_event(
    public_error: PublicError,
    exc: Exception | MetadataIngestDriveFailure,
    group_id: str,
    progress: MetadataIngestProgress | MetadataIngestSkipped | None,
    operation_id: str | None,
) -> StreamEvent:
    drive_id = exc.drive_id if isinstance(exc, MetadataIngestDriveFailure) else None
    return StreamEvent(
        MetadataIngestEventStatus.ERROR.value,
        {
            "status": MetadataIngestEventStatus.ERROR.value,
            "operation_id": operation_id,
            "error": public_error.as_dict(),
            "context": {
                "operation": "metadata.ingest",
                "group_id": group_id,
                "drive_id": drive_id,
                "operation_id": operation_id,
            },
            "progress": {"processed": progress.processed if progress else 0},
        },
        StreamEventKind.ERROR,
    )


@router.post(
    "/groups/{group_id}/metadata",
    response_class=EventSourceResponse,
    summary="Ingest metadata from a member group's selected drives",
)
async def ingest_group_metadata_endpoint(
    group_id: str,
    request: MetadataIngestRequestBody,
    user_id: Annotated[str, Depends(get_current_graph_user_id)],
    drive_source: Annotated[GroupDriveSource, Depends(get_group_drive_source)],
    source: Annotated[DocumentRefreshSource, Depends(get_document_metadata_source)],
    store: Annotated[DocumentRefreshStore, Depends(get_document_store)],
) -> AsyncGenerator[ServerSentEvent]:
    last_update: MetadataIngestProgress | MetadataIngestSkipped | None = None
    operation_id: str | None = None
    missing_outcome = _failure_event(
        internal_error(),
        RuntimeError("Metadata inventory ended without an outcome"),
        group_id,
        None,
        None,
    )
    lifecycle = StreamLifecycle(
        ProgressEventLimiter(config.stream_progress_interval_seconds),
        missing_outcome,
    )
    selection = MetadataIngestRequest(
        group_id,
        [
            DriveMetadataSelector(
                selector.drive_id, selector.paths, selector.extensions
            )
            for selector in request.include
        ],
        [
            DriveMetadataSelector(
                selector.drive_id, selector.paths, selector.extensions
            )
            for selector in request.exclude
        ],
    )
    updates = ingest_group_metadata(user_id, selection, drive_source, source, store)

    try:
        async for update in updates:
            if isinstance(update, MetadataIngestStarted):
                operation_id = str(update.operation_id)
            if isinstance(update, MetadataIngestProgress | MetadataIngestSkipped):
                last_update = update
            response = lifecycle.encode(_event(update, group_id, operation_id))
            if response is not None:
                yield response
    except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on failures
        public_error = MetadataErrorMapper.describe(exc)
        log_api_failure(
            "metadata",
            exc,
            public_error,
            identifiers={
                "operation": "metadata.ingest",
                "group_id": group_id,
                "drive_id": exc.drive_id
                if isinstance(exc, MetadataIngestDriveFailure)
                else None,
            },
        )
        response = lifecycle.encode(
            _failure_event(public_error, exc, group_id, last_update, operation_id)
        )
        if response is not None:
            yield response
    else:
        response = lifecycle.finish()
        if response is not None:
            log_missing_outcome(
                "metadata",
                identifiers={"operation": "metadata.ingest", "group_id": group_id},
            )
            yield response
    finally:
        # Close nested ingestion so cancellation discards unfinished staging.
        with anyio.CancelScope(shield=True):
            await updates.aclose()
