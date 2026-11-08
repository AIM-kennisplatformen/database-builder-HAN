from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
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
    summary="Sync employees from external source",
    description=(
        "Stream SSE progress events after each employee is saved, followed by a "
        "complete or error event. The total respects the requested limit."
    ),
)
async def sync_employees_endpoint(
    request: EmployeeSyncRequestBody,
    source: Annotated[EmployeeSource, Depends(get_employee_source)],
    store: Annotated[EmployeeStore, Depends(get_employee_store)],
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        processed = 0
        total: int | None = None
        available = 0

        def format_event(event: EmployeeSyncProgressEvent) -> str:
            return (
                f"event: {event.status}\n"
                f"data: {event.model_dump_json(exclude_none=True)}\n\n"
            )

        try:
            async for update in sync_employees(
                SyncEmployeesRequest(request.limit), source, store
            ):
                processed = update.processed
                total = update.total
                available = update.available
                yield format_event(
                    EmployeeSyncProgressEvent(
                        status="progress",
                        processed=processed,
                        total=total,
                        available=available,
                    )
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
            yield format_event(
                EmployeeSyncProgressEvent(
                    status="error",
                    processed=processed,
                    total=total,
                    available=available,
                    error_status=status,
                    detail=str(exc),
                )
            )
        except Exception:  # noqa: BLE001 - terminate the SSE stream on unexpected failures
            yield format_event(
                EmployeeSyncProgressEvent(
                    status="error",
                    processed=processed,
                    total=total,
                    available=available,
                    error_status=500,
                    detail="Employee sync failed",
                )
            )
        else:
            yield format_event(
                EmployeeSyncProgressEvent(
                    status="complete",
                    processed=processed,
                    total=total,
                    available=available,
                )
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
