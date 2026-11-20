from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class OperationRun:
    id: UUID
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, object]
    failure_code: str | None
    domain_ids: dict[str, object]


class OperationRunReader(Protocol):
    async def get_operation_run(
        self, operation_id: UUID, principal_id: str
    ) -> OperationRun | None: ...
