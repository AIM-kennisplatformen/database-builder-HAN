from collections.abc import Sequence
from typing import Protocol

from domain.models.ubwfris import UbwfrisRow


class UbwfrisWriter[T: UbwfrisRow](Protocol):
    async def begin_snapshot(self, snapshot_id: str) -> None: ...

    async def publish_snapshot(self, snapshot_id: str) -> None: ...

    async def stage_ubwfris_rows(self, snapshot_id: str, rows: Sequence[T]) -> int: ...

    async def discard_snapshot(self, snapshot_id: str) -> None: ...
