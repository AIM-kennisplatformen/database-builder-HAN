from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Annotated

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


class EmployeeSyncProgressStatus(StrEnum):
    PROGRESS = "progress"
    COMPLETE = "complete"
    ERROR = "error"


class EmployeeSyncProgressEvent(BaseModel):
    status: EmployeeSyncProgressStatus
    processed: int
    total: int | None = None
    available: int
    created: int
    updated: int
    unchanged: int
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
    created = 0
    updated = 0
    unchanged = 0

    try:
        async for update in sync_employees(
            SyncEmployeesRequest(request.limit), source, store
        ):
            processed = update.processed
            total = update.total
            available = update.available
            created = update.created
            updated = update.updated
            unchanged = update.unchanged

            yield EmployeeSyncProgressEvent(
                status=EmployeeSyncProgressStatus.PROGRESS,
                processed=update.processed,
                total=update.total,
                available=update.available,
                created=update.created,
                updated=update.updated,
                unchanged=update.unchanged,
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
            status=EmployeeSyncProgressStatus.ERROR,
            processed=processed,
            total=total,
            available=available,
            created=created,
            updated=updated,
            unchanged=unchanged,
            error_status=status,
            detail=str(exc),
        )
    except Exception:  # noqa: BLE001 - terminate the JSONL stream on unexpected failures
        yield EmployeeSyncProgressEvent(
            status=EmployeeSyncProgressStatus.ERROR,
            processed=processed,
            total=total,
            available=available,
            created=created,
            updated=updated,
            unchanged=unchanged,
            error_status=500,
            detail="Employee sync failed",
        )
    else:
        yield EmployeeSyncProgressEvent(
            status=EmployeeSyncProgressStatus.COMPLETE,
            processed=processed,
            total=total,
            available=available,
            created=created,
            updated=updated,
            unchanged=unchanged,
        )
