from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from frozendict import frozendict
from pydantic import BaseModel, Field

from api.progress import ProgressEventLimiter
from api.stream import StreamEvent, StreamEventKind, StreamLifecycle
from config import config
from dependencies import get_employee_source, get_employee_store
from domain.exceptions.employee_sync_errors import (
    InvalidLimit,
    NoEmployeesFound,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.ports.employees.employee_source import EmployeeSource
from domain.ports.employees.employee_store import EmployeeWriter
from domain.use_cases.employee_sync import (
    EmployeesSyncProgress,
    EmployeesSyncRequest,
    EmployeesSyncResult,
    sync_employees,
)

router = APIRouter()

EMPLOYEE_SYNC_ERROR_STATUS: frozendict[type[Exception], int] = frozendict(
    {
        InvalidLimit: 422,
        NoEmployeesFound: 404,
        SourceUnavailable: 501,
        StoreUnavailable: 503,
    }
)


class EmployeesSyncRequestBody(BaseModel):
    limit: int | None = Field(default=None, gt=0)


class EmployeesSyncProgressStatus(StrEnum):
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"


class EmployeesSyncProgressEvent(BaseModel):
    status: EmployeesSyncProgressStatus
    processed: int
    total: int | None = None
    available: int
    created: int
    updated: int
    unchanged: int
    error_status: int | None = None
    detail: str | None = None


def _event(update: EmployeesSyncProgress | EmployeesSyncResult) -> StreamEvent:
    if isinstance(update, EmployeesSyncResult):
        status = EmployeesSyncProgressStatus.COMPLETE
        kind = StreamEventKind.COMPLETE
    else:
        status = EmployeesSyncProgressStatus.PROGRESS
        kind = StreamEventKind.PROGRESS

    event = EmployeesSyncProgressEvent(
        status=status,
        processed=update.processed,
        total=update.total,
        available=update.available,
        created=update.created,
        updated=update.updated,
        unchanged=update.unchanged,
    )
    return StreamEvent(
        event.status.value,
        event.model_dump(exclude_none=True),
        kind,
    )


def _failure_event(
    exc: Exception,
    progress: EmployeesSyncProgress | None,
) -> StreamEvent:
    error_status = 500
    detail = "Employee sync failed"
    for error_type, status in EMPLOYEE_SYNC_ERROR_STATUS.items():
        if isinstance(exc, error_type):
            error_status = status
            detail = str(exc)
            break
    event = EmployeesSyncProgressEvent(
        status=EmployeesSyncProgressStatus.ERROR,
        processed=progress.processed if progress else 0,
        total=progress.total if progress else None,
        available=progress.available if progress else 0,
        created=progress.created if progress else 0,
        updated=progress.updated if progress else 0,
        unchanged=progress.unchanged if progress else 0,
        error_status=error_status,
        detail=detail,
    )
    return StreamEvent(
        event.status.value,
        event.model_dump(exclude_none=True),
        StreamEventKind.ERROR,
    )


@router.post(
    path="/sync",
    response_class=EventSourceResponse,
    summary="Sync employees from external source",
    description=(
        "Stream rate-limited progress events as employees are saved, followed by "
        "a complete or error event. The total respects the requested limit."
    ),
)
async def sync_employees_endpoint(
    request: EmployeesSyncRequestBody,
    source: Annotated[EmployeeSource, Depends(get_employee_source)],
    store: Annotated[EmployeeWriter, Depends(get_employee_store)],
) -> AsyncGenerator[ServerSentEvent]:
    missing_outcome = _failure_event(
        RuntimeError("Employee sync ended without an outcome"),
        None,
    )
    lifecycle = StreamLifecycle(
        ProgressEventLimiter(config.stream_progress_interval_seconds),
        missing_outcome,
    )
    updates = sync_employees(EmployeesSyncRequest(request.limit), source, store)
    last_progress: EmployeesSyncProgress | None = None
    try:
        async for update in updates:
            if isinstance(update, EmployeesSyncProgress):
                last_progress = update
            event = _event(update)
            response = lifecycle.encode(event)
            if response is not None:
                yield response
    except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on failures
        response = lifecycle.encode(_failure_event(exc, last_progress))
        if response is not None:
            yield response
    else:
        response = lifecycle.finish()
        if response is not None:
            yield response
    finally:
        with anyio.CancelScope(shield=True):
            await updates.aclose()
