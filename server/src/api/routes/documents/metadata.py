from collections.abc import AsyncIterator
from enum import StrEnum
from traceback import format_exception
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from api.progress import ProgressEventLimiter
from config import config
from dependencies import get_document_metadata_source
from domain.exceptions.metadata_inventory_errors import (
    MetadataSourceError,
    SourceUnavailable,
)
from domain.ports.sources.document_metadata_source import DocumentMetadataSource
from domain.use_cases.metadata_inventory import (
    InventoryMetadataProgress,
    InventoryMetadataRequest,
    inventory_metadata,
)

router = APIRouter()

type InventoryError = (
    ValueError | MetadataSourceError | SourceUnavailable | RuntimeError
)


class InventoryMetadataRequestBody(BaseModel):
    drive_id: str = Field(min_length=1)
    extensions: list[str] = Field(min_length=1)
    limit: int | None = Field(default=None, gt=0)


class InventoryMetadataEventStatus(StrEnum):
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"


class InventoryMetadataEvent(BaseModel):
    status: InventoryMetadataEventStatus
    processed: int
    drive_id: str
    item_id: str | None = None
    name: str | None = None
    error_status: int | None = None
    detail: str | None = None
    traceback: str | None = None


def _sse_event(event: InventoryMetadataEvent) -> ServerSentEvent:
    return ServerSentEvent(
        event=event.status.value,
        data=event.model_dump(exclude_none=True),
    )


def _error_status(exc: InventoryError) -> int:
    if isinstance(exc, ValueError):
        return 422
    if isinstance(exc, MetadataSourceError):
        return exc.status_code if 400 <= exc.status_code < 500 else 502
    if isinstance(exc, SourceUnavailable):
        return 503
    return 500


def _event(
    update: InventoryMetadataProgress, drive_id: str, processed: int
) -> ServerSentEvent:
    return _sse_event(
        InventoryMetadataEvent(
            status=InventoryMetadataEventStatus.PROGRESS,
            processed=processed,
            drive_id=drive_id,
            item_id=update.document.id.id[1],
            name=update.document.file_metadata.name,
        )
    )


def _failure_event(
    exc: InventoryError,
    drive_id: str,
    processed: int,
    *,
    traceback_exc: BaseException | None = None,
) -> ServerSentEvent:
    return _sse_event(
        InventoryMetadataEvent(
            status=InventoryMetadataEventStatus.ERROR,
            processed=processed,
            drive_id=drive_id,
            error_status=_error_status(exc),
            detail=str(exc),
            traceback="".join(format_exception(traceback_exc or exc))
            if config.debug
            else None,
        )
    )


@router.post(
    "/metadata",
    response_class=EventSourceResponse,
    summary="Inventory document metadata",
)
async def inventory_metadata_endpoint(
    request: InventoryMetadataRequestBody,
    source: Annotated[DocumentMetadataSource, Depends(get_document_metadata_source)],
) -> AsyncIterator[ServerSentEvent]:
    processed = 0
    progress_limiter = ProgressEventLimiter(config.stream_progress_interval_seconds)

    try:
        async for update in inventory_metadata(
            InventoryMetadataRequest(
                request.drive_id, request.extensions, request.limit
            ),
            source,
        ):
            processed += 1
            if not progress_limiter.should_emit():
                continue
            yield _event(update, request.drive_id, processed)
    except (ValueError, MetadataSourceError, SourceUnavailable) as exc:
        yield _failure_event(exc, request.drive_id, processed)
    except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on unexpected failures
        yield _failure_event(
            RuntimeError("Metadata inventory failed"),
            request.drive_id,
            processed,
            traceback_exc=exc,
        )
    else:
        yield _sse_event(
            InventoryMetadataEvent(
                status=InventoryMetadataEventStatus.COMPLETE,
                processed=processed,
                drive_id=request.drive_id,
            )
        )
