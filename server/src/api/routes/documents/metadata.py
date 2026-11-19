from collections.abc import AsyncIterator
from enum import StrEnum
from traceback import format_exception
from typing import Annotated, ClassVar

import anyio
from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from api.progress import ProgressEventLimiter
from config import config
from dependencies import (
    get_current_graph_user_id,
    get_document_metadata_source,
    get_document_store,
    get_group_drive_source,
)
from domain.exceptions.metadata_ingest_errors import (
    MetadataRunAlreadyActive,
    MetadataSourceError,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import DocumentRefreshStore
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import GroupNotAccessible
from domain.use_cases.metadata_ingest import (
    DriveMetadataSelector,
    MetadataIngestDriveFailure,
    MetadataIngestProgress,
    MetadataIngestRequest,
    MetadataIngestSkipped,
    ingest_group_metadata,
)

router = APIRouter()

type MetadataIngestError = (
    GroupNotAccessible
    | ValueError
    | MetadataSourceError
    | SourceUnavailable
    | StoreUnavailable
    | RuntimeError
    | MetadataRunAlreadyActive
)


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
    traceback: str | None = None


def _sse_event(event: MetadataIngestEvent) -> ServerSentEvent:
    return ServerSentEvent(
        event=event.status.value, data=event.model_dump(exclude_none=True)
    )


def _error_status(exc: MetadataIngestError) -> int:
    if isinstance(exc, MetadataRunAlreadyActive):
        return 409
    if isinstance(exc, GroupNotAccessible):
        return 404
    if isinstance(exc, ValueError):
        return 422
    if isinstance(exc, MetadataSourceError):
        return exc.status_code if 400 <= exc.status_code < 500 else 502
    if isinstance(exc, (SourceUnavailable, StoreUnavailable)):
        return 503
    return 500


def _failure_event(
    exc: Exception | MetadataIngestDriveFailure,
    group_id: str,
    processed: int,
) -> ServerSentEvent:
    drive_id: str | None = None
    original: Exception = exc
    if isinstance(exc, MetadataIngestDriveFailure):
        drive_id = exc.drive_id
        original = exc.cause

    if isinstance(
        original,
        (
            GroupNotAccessible,
            ValueError,
            MetadataSourceError,
            MetadataRunAlreadyActive,
            SourceUnavailable,
            StoreUnavailable,
        ),
    ):
        failure: MetadataIngestError = original
    else:
        failure = RuntimeError("Metadata inventory failed")

    return _sse_event(
        MetadataIngestEvent(
            status=MetadataIngestEventStatus.ERROR,
            group_id=group_id,
            drive_id=drive_id,
            processed=processed,
            error_status=_error_status(failure),
            detail=str(failure),
            traceback="".join(format_exception(original))
            if config.debug
            else None,
        )
    )


def _event(
    update: MetadataIngestProgress | MetadataIngestSkipped | None,
    group_id: str,
    processed: int,
    skipped_drive_ids: list[str],
) -> ServerSentEvent:
    if update is None:
        return _sse_event(
            MetadataIngestEvent(
                status=MetadataIngestEventStatus.COMPLETE,
                group_id=group_id,
                processed=processed,
                skipped_drive_ids=skipped_drive_ids,
            )
        )
    if isinstance(update, MetadataIngestSkipped):
        return _sse_event(
            MetadataIngestEvent(
                status=MetadataIngestEventStatus.SKIPPED,
                group_id=group_id,
                drive_id=update.drive_id,
                processed=processed,
                error_status=403,
                detail=update.reason,
            )
        )
    progress = update.update
    return _sse_event(
        MetadataIngestEvent(
            status=MetadataIngestEventStatus.PROGRESS,
            group_id=group_id,
            drive_id=update.drive_id,
            processed=processed,
            total=progress.total,
            available=progress.available,
            item_id=progress.document.id.id[1],
            name=progress.document.file_metadata.name,
        )
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
) -> AsyncIterator[ServerSentEvent]:
    processed = 0
    skipped_drive_ids: list[str] = []
    progress_limiter = ProgressEventLimiter(config.stream_progress_interval_seconds)
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
        async for progress in updates:
            if isinstance(progress, MetadataIngestSkipped):
                skipped_drive_ids.append(progress.drive_id)
                yield _event(progress, group_id, processed, skipped_drive_ids)
                continue
            processed += 1
            if not progress_limiter.should_emit():
                continue
            yield _event(progress, group_id, processed, skipped_drive_ids)
    except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on failures
        yield _failure_event(exc, group_id, processed)
    else:
        yield _event(None, group_id, processed, skipped_drive_ids)
    finally:
        # Close the nested ingestion generator so per-drive staging is discarded
        # when the client disconnects.
        with anyio.CancelScope(shield=True):
            await updates.aclose()
