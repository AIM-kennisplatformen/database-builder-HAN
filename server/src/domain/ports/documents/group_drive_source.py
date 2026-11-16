from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UserGroup:
    id: str
    name: str | None


class GroupDriveSource(Protocol):
    def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]: ...

    def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]: ...
