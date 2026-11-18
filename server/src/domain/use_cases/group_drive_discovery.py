from dataclasses import dataclass

from domain.ports.documents.group_drive_source import (
    GroupDriveLocation,
    GroupDriveSource,
    UserGroup,
)


class GroupNotAccessible(ValueError):
    """The group is not among the caller's direct memberships."""


@dataclass(frozen=True)
class GroupDrives:
    group_id: str
    group_name: str | None
    locations: tuple[GroupDriveLocation, ...]

    @property
    def drive_ids(self) -> tuple[str, ...]:
        return tuple(location.drive_id for location in self.locations)


async def get_user_groups(
    user_id: str, source: GroupDriveSource
) -> tuple[UserGroup, ...]:
    if not user_id.strip():
        raise ValueError("user_id is required")
    groups: dict[str, UserGroup] = {}
    async for group in source.stream_user_groups(user_id):
        _ = groups.setdefault(group.id, group)
    return tuple(groups.values())


async def get_user_group_drives(
    user_id: str, source: GroupDriveSource
) -> tuple[GroupDrives, ...]:
    groups_with_drives: list[GroupDrives] = []
    for group in await get_user_groups(user_id, source):
        locations: dict[str, GroupDriveLocation] = {}
        async for location in source.stream_group_drive_locations(group.id):
            _ = locations.setdefault(location.drive_id, location)
        if locations:
            groups_with_drives.append(
                GroupDrives(group.id, group.name, tuple(locations.values()))
            )
    return tuple(groups_with_drives)


async def get_group_drives(
    user_id: str, group_id: str, source: GroupDriveSource
) -> GroupDrives:
    if not group_id.strip():
        raise ValueError("group_id is required")
    group = next(
        (
            group
            for group in await get_user_groups(user_id, source)
            if group.id == group_id
        ),
        None,
    )
    if group is None:
        raise GroupNotAccessible("Group is not accessible to the caller")
    locations: dict[str, GroupDriveLocation] = {}
    async for location in source.stream_group_drive_locations(group_id):
        _ = locations.setdefault(location.drive_id, location)
    return GroupDrives(group.id, group.name, tuple(locations.values()))
