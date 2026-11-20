from collections.abc import Sequence
from typing import Literal, Protocol
from uuid import UUID

from domain.models.ubwfris import UbwfrisRow


class UbwfrisWriter[T: UbwfrisRow](Protocol):
    async def begin_operation(
        self, operation_id: UUID, principal_id: str, snapshot_id: str
    ) -> None: ...

    async def begin_snapshot(self, snapshot_id: str) -> None: ...

    async def publish_snapshot(
        self, snapshot_id: str, operation_id: UUID, summary: dict[str, object]
    ) -> None: ...

    async def fail_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        summary: dict[str, object],
        *,
        interrupted: bool = False,
    ) -> None: ...

    async def stage_ubwfris_rows(self, snapshot_id: str, rows: Sequence[T]) -> int: ...

    async def discard_snapshot(self, snapshot_id: str) -> None: ...


class UbwfrisSnapshotDeleter(Protocol):
    async def delete_all_snapshots(self, row_type: Literal["ib", "rha"]) -> bool: ...
