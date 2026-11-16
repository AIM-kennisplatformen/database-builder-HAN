from dataclasses import dataclass

from domain.ports.documents.group_drive_source import GroupDriveSource


@dataclass(frozen=True)
class GroupDrives:
    group_id: str
    group_name: str | None
    drive_ids: tuple[str, ...]


async def get_user_group_drives(
    user_id: str, source: GroupDriveSource
) -> tuple[GroupDrives, ...]:
    if not user_id.strip():
        raise ValueError("user_id is required")

    group_ids: set[str] = set()
    groups: list[GroupDrives] = []
    async for group in source.stream_user_groups(user_id):
        if group.id in group_ids:
            continue
        group_ids.add(group.id)
        drive_ids: dict[str, None] = {}
        async for drive_id in source.stream_group_drive_ids(group.id):
            drive_ids[drive_id] = None
        if drive_ids:
            groups.append(GroupDrives(group.id, group.name, tuple(drive_ids)))
    return tuple(groups)
