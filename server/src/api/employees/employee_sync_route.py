from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from dependencies import get_employee_source, get_employee_store
from domain.exceptions.employee_sync_errors import (
    InvalidLimit,
    NoEmployeesFound,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.employee import SyncEmployeesRequest
from domain.ports.persistence.employee_store import EmployeeStore
from domain.ports.sources.employee_source import EmployeeSource
from domain.use_cases.employee_sync import sync_employees

router = APIRouter(tags=["Employees"], prefix="/api/v1")


class EmployeeSyncRequestBody(BaseModel):
    limit: int | None = Field(default=None, gt=0)


class EmployeeSyncProgressEvent(BaseModel):
    status: Literal["progress", "complete", "error"]
    processed: int
    total: int | None = None
    available: int
    error_status: int | None = None
    detail: str | None = None


@router.post(
    path="/employees/sync",
    response_model_exclude_none=True,
    summary="Sync employees from external source",
    description=(
        "Stream JSON Lines progress records after each employee is saved, followed "
        "by a complete or error record. The total respects the requested limit."
    ),
)
async def sync_employees_endpoint(
    request: EmployeeSyncRequestBody,
    source: Annotated[EmployeeSource, Depends(get_employee_source)],
    store: Annotated[EmployeeStore, Depends(get_employee_store)],
) -> AsyncIterator[EmployeeSyncProgressEvent]:
    processed = 0
    total: int | None = None
    available = 0

    try:
        async for update in sync_employees(
            SyncEmployeesRequest(request.limit), source, store
        ):
            processed = update.processed
            total = update.total
            available = update.available
            yield EmployeeSyncProgressEvent(
                status="progress",
                processed=processed,
                total=total,
                available=available,
            )
    except (
        InvalidLimit,
        NoEmployeesFound,
        SourceUnavailable,
        StoreUnavailable,
    ) as exc:
        status = {
            InvalidLimit: 422,
            NoEmployeesFound: 404,
            SourceUnavailable: 501,
            StoreUnavailable: 503,
        }[type(exc)]
        yield EmployeeSyncProgressEvent(
            status="error",
            processed=processed,
            total=total,
            available=available,
            error_status=status,
            detail=str(exc),
        )
    except Exception:  # noqa: BLE001 - terminate the JSONL stream on unexpected failures
        yield EmployeeSyncProgressEvent(
            status="error",
            processed=processed,
            total=total,
            available=available,
            error_status=500,
            detail="Employee sync failed",
        )
    else:
        yield EmployeeSyncProgressEvent(
            status="complete",
            processed=processed,
            total=total,
            available=available,
        )
