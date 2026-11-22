from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import Annotated, ClassVar

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from api.errors import (
    PublicError,
    build_internal_error,
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
from domain.use_cases.metadata_refresh import (
    MetadataDriveIngestFailure,
    MetadataDriveSelector,
    MetadataGroupIngestProgress,
    MetadataGroupIngestRequest,
    MetadataGroupIngestResult,
    MetadataGroupIngestSkipped,
    MetadataGroupIngestStarted,
)
from domain.use_cases.metadata_refresh.refresh import refresh_group_metadata_atomically

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


def _build_event(
    event: MetadataGroupIngestStarted
    | MetadataGroupIngestProgress
    | MetadataGroupIngestSkipped
    | MetadataGroupIngestResult,
    group_id: str,
    operation_id: str | None,
) -> StreamEvent:
    match event:
        case MetadataGroupIngestStarted(operation_id=started_operation_id):
            return StreamEvent(
                "started",
                {
                    "status": "running",
                    "group_id": group_id,
                    "operation_id": str(started_operation_id),
                },
                StreamEventKind.NON_TERMINAL,
            )
        case MetadataGroupIngestResult(processed=processed_count, skipped_drive_ids=skipped):
            api_event = MetadataIngestEvent(
                status=MetadataIngestEventStatus.COMPLETE,
                group_id=group_id,
                processed=processed_count,
                skipped_drive_ids=list(skipped),
                operation_id=operation_id,
            )
            return StreamEvent(
                api_event.status.value,
                api_event.model_dump(exclude_none=True),
                StreamEventKind.COMPLETE,
            )
        case MetadataGroupIngestSkipped(processed=processed_count, drive_id=drive_id):
            api_event = MetadataIngestEvent(
                status=MetadataIngestEventStatus.SKIPPED,
                group_id=group_id,
                processed=processed_count,
                drive_id=drive_id,
                error_status=403,
                detail="Drive is not accessible",
                operation_id=operation_id,
            )
            return StreamEvent(
                api_event.status.value,
                api_event.model_dump(exclude_none=True),
                StreamEventKind.NON_TERMINAL,
            )
        case MetadataGroupIngestProgress(
            processed=processed_count, drive_id=drive_id, progress=progress
        ):
            api_event = MetadataIngestEvent(
                status=MetadataIngestEventStatus.PROGRESS,
                group_id=group_id,
                processed=processed_count,
                drive_id=drive_id,
                total=progress.total,
                available=progress.available,
                item_id=progress.document.id.id[1],
                name=progress.document.file_metadata.name,
                operation_id=operation_id,
            )
            return StreamEvent(
                api_event.status.value,
                api_event.model_dump(exclude_none=True),
                StreamEventKind.PROGRESS,
            )


def _build_failure_event(
    public_error: PublicError,
    exc: Exception | MetadataDriveIngestFailure,
    group_id: str,
    last_progress_event: MetadataGroupIngestProgress | MetadataGroupIngestSkipped | None,
    operation_id: str | None,
) -> StreamEvent:
    drive_id = exc.drive_id if isinstance(exc, MetadataDriveIngestFailure) else None
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
            "progress": {
                "processed": (
                    last_progress_event.processed if last_progress_event else 0
                )
            },
        },
        StreamEventKind.ERROR,
    )


def _build_refresh_event_context(
    event: MetadataGroupIngestStarted
    | MetadataGroupIngestProgress
    | MetadataGroupIngestSkipped
    | MetadataGroupIngestResult,
    operation_id: str | None,
    last_progress_event: MetadataGroupIngestProgress | MetadataGroupIngestSkipped | None,
) -> tuple[str | None, MetadataGroupIngestProgress | MetadataGroupIngestSkipped | None]:
    match event:
        case MetadataGroupIngestStarted(operation_id=started_operation_id):
            return str(started_operation_id), last_progress_event
        case MetadataGroupIngestProgress() | MetadataGroupIngestSkipped():
            return operation_id, event
        case MetadataGroupIngestResult():
            return operation_id, last_progress_event


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
    last_progress_event: MetadataGroupIngestProgress | MetadataGroupIngestSkipped | None = None
    operation_id: str | None = None
    missing_outcome = _build_failure_event(
        build_internal_error(),
        RuntimeError("Metadata inventory ended without an outcome"),
        group_id,
        None,
        None,
    )
    lifecycle = StreamLifecycle(
        ProgressEventLimiter(config.stream_progress_interval_seconds),
        missing_outcome,
    )
    selection = MetadataGroupIngestRequest(
        group_id,
        [
            MetadataDriveSelector(
                selector.drive_id, selector.paths, selector.extensions
            )
            for selector in request.include
        ],
        [
            MetadataDriveSelector(
                selector.drive_id, selector.paths, selector.extensions
            )
            for selector in request.exclude
        ],
    )
    refresh_events = refresh_group_metadata_atomically(
        user_id, selection, drive_source, source, store
    )

    try:
        async for event in refresh_events:
            operation_id, last_progress_event = _build_refresh_event_context(
                event, operation_id, last_progress_event
            )
            response = lifecycle.encode(_build_event(event, group_id, operation_id))
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
                if isinstance(exc, MetadataDriveIngestFailure)
                else None,
            },
        )
        response = lifecycle.encode(
            _build_failure_event(
                public_error, exc, group_id, last_progress_event, operation_id
            )
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
            await refresh_events.aclose()
