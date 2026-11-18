from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class UserGroup:
    id: str
    name: str | None


@dataclass(frozen=True)
class GroupDriveLocation:
    drive_id: str
    root_item_id: str = "root"


class GroupDriveSource(Protocol):
    def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]: ...

    def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]: ...

    def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]: ...
