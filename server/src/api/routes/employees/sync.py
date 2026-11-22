from collections.abc import AsyncGenerator
from enum import StrEnum
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from api.errors import (
    PublicError,
    build_internal_error,
    log_api_failure,
    log_missing_outcome,
)
from api.progress import ProgressEventLimiter
from api.stream import StreamEvent, StreamEventKind, StreamLifecycle
from config import config
from dependencies import (
    get_current_graph_user_id,
    get_employee_data_deleter,
    get_employee_source,
    get_employee_store,
    require_employee_delete_enabled,
)
from domain.exceptions.employee_sync_errors import (
    EmployeeSourceUnavailable,
    EmployeeStoreUnavailable,
    InvalidLimit,
    NoEmployeesFound,
)
from domain.ports.employees.employee_source import EmployeeSource
from domain.ports.employees.employee_store import EmployeeDataDeleter, EmployeeWriter
from domain.use_cases.employee_sync import (
    EmployeeSyncProgress,
    EmployeeSyncRequest,
    EmployeeSyncResult,
    EmployeeSyncStarted,
    sync_employees,
)

router = APIRouter()


@router.delete(
    "/sync",
    summary="Delete all synced employee data",
    include_in_schema=config.delete_endpoints.employee,
)
async def delete_employee_data(
    _delete_enabled: Annotated[None, Depends(require_employee_delete_enabled)],
    _principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    store: Annotated[EmployeeDataDeleter, Depends(get_employee_data_deleter)],
) -> dict[str, bool]:
    if not await store.delete_all_employee_data():
        raise HTTPException(
            status_code=409,
            detail="Employee data cannot be reset while a sync is active or overrides exist",
        )
    return {"deleted": True}


class EmployeesSyncRequestBody(BaseModel):
    limit: int | None = Field(default=None, gt=0)


class EmployeesSyncProgressStatus(StrEnum):
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"


class EmployeesSyncProgressEvent(BaseModel):
    status: EmployeesSyncProgressStatus
    processed: int = Field(
        description="Employees staged so far; progress does not mean they are committed."
    )
    total: int | None = None
    available: int
    created: int = Field(
        description=(
            "Provisional count so far. Counts become committed only in the complete event."
        )
    )
    updated: int = Field(
        description=(
            "Provisional count so far. Counts become committed only in the complete event."
        )
    )
    unchanged: int = Field(
        description=(
            "Provisional count so far. Counts become committed only in the complete event."
        )
    )
    operation_id: str | None = None


class EmployeeErrorMapper:
    @staticmethod
    def describe(exc: Exception) -> PublicError:
        if isinstance(exc, InvalidLimit):
            return PublicError("employee.invalid_limit", 422, str(exc))
        if isinstance(exc, NoEmployeesFound):
            return PublicError("employee.no_employees", 404, "No employees were found")
        if isinstance(exc, EmployeeSourceUnavailable):
            return PublicError(
                "employee.source_unavailable", 501, "Employee source is unavailable"
            )
        if isinstance(exc, EmployeeStoreUnavailable):
            return PublicError(
                "employee.store_unavailable", 503, "Employee store is unavailable"
            )
        return build_internal_error()


def _build_event(
    update: EmployeeSyncStarted | EmployeeSyncProgress | EmployeeSyncResult,
) -> StreamEvent:
    if isinstance(update, EmployeeSyncStarted):
        return StreamEvent(
            "started",
            {"status": "running", "operation_id": str(update.operation_id)},
            StreamEventKind.NON_TERMINAL,
        )
    if isinstance(update, EmployeeSyncResult):
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
        operation_id=str(update.operation_id),
    )
    return StreamEvent(
        event.status.value,
        event.model_dump(exclude_none=True),
        kind,
    )


def _build_failure_event(
    public_error: PublicError,
    progress: EmployeeSyncProgress | None,
    operation_id: str | None,
) -> StreamEvent:
    return StreamEvent(
        EmployeesSyncProgressStatus.ERROR.value,
        {
            "status": EmployeesSyncProgressStatus.ERROR.value,
            "operation_id": operation_id,
            "error": public_error.as_dict(),
            "context": {"operation": "employee.sync", "operation_id": operation_id},
            "progress": {
                "processed": progress.processed if progress else 0,
                "total": progress.total if progress else None,
                "available": progress.available if progress else 0,
                "created": progress.created if progress else 0,
                "updated": progress.updated if progress else 0,
                "unchanged": progress.unchanged if progress else 0,
            },
        },
        StreamEventKind.ERROR,
    )


@router.post(
    path="/sync",
    response_class=EventSourceResponse,
    summary="Sync employees from external source",
    description=(
        "Stream rate-limited progress as employees are staged, followed by a "
        "complete or error event. Progress counts are provisional; only the "
        "complete event reports committed counts. The total respects the "
        "requested limit."
    ),
)
async def sync_employees_endpoint(
    request: EmployeesSyncRequestBody,
    principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    source: Annotated[EmployeeSource, Depends(get_employee_source)],
    store: Annotated[EmployeeWriter, Depends(get_employee_store)],
) -> AsyncGenerator[ServerSentEvent]:
    missing_outcome = _build_failure_event(build_internal_error(), None, None)
    lifecycle = StreamLifecycle(
        ProgressEventLimiter(config.stream_progress_interval_seconds),
        missing_outcome,
    )
    updates = sync_employees(
        EmployeeSyncRequest(request.limit), source, store, principal_id
    )
    last_progress: EmployeeSyncProgress | None = None
    operation_id: str | None = None
    try:
        async for update in updates:
            if isinstance(update, EmployeeSyncStarted):
                operation_id = str(update.operation_id)
            if isinstance(update, EmployeeSyncProgress):
                last_progress = update
            event = _build_event(update)
            response = lifecycle.encode(event)
            if response is not None:
                yield response
    except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on failures
        public_error = EmployeeErrorMapper.describe(exc)
        log_api_failure(
            "employee",
            exc,
            public_error,
            identifiers={"operation": "employee.sync"},
        )
        response = lifecycle.encode(
            _build_failure_event(public_error, last_progress, operation_id)
        )
        if response is not None:
            yield response
    else:
        response = lifecycle.finish()
        if response is not None:
            log_missing_outcome("employee", identifiers={"operation": "employee.sync"})
            yield response
    finally:
        with anyio.CancelScope(shield=True):
            await updates.aclose()
