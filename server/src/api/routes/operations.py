from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_current_graph_user_id, get_operation_run_reader
from domain.ports.operation_run_store import OperationRunReader

router = APIRouter(prefix="/api/v1/operations", tags=["Operations"])


class OperationRunResponse(BaseModel):
    id: UUID
    kind: str
    status: str
    started_at: str
    finished_at: str | None
    summary: dict[str, object]
    failure_code: str | None
    domain_ids: dict[str, object]


@router.get("/{operation_id}", response_model=OperationRunResponse)
async def get_operation_run(
    operation_id: UUID,
    principal_id: Annotated[str, Depends(get_current_graph_user_id)],
    store: Annotated[OperationRunReader, Depends(get_operation_run_reader)],
) -> OperationRunResponse:
    run = await store.get_operation_run(operation_id, principal_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Operation run not found")
    return OperationRunResponse(
        id=run.id,
        kind=run.kind,
        status=run.status,
        started_at=run.started_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        summary=run.summary,
        failure_code=run.failure_code,
        domain_ids=run.domain_ids,
    )
