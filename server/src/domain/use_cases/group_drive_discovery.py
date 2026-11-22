from dataclasses import dataclass

from domain.ports.documents.group_drive_source import (
    GroupDriveLocation,
    GroupDriveSource,
    UserGroup,
)


class GroupDriveNotAccessible(ValueError):
    """Raised when a group is not among the caller's direct memberships."""


@dataclass(frozen=True)
class GroupDriveCollection:
    """Collect a group and its discovered drive locations."""

    group_id: str
    group_name: str | None
    locations: tuple[GroupDriveLocation, ...]

    @property
    def drive_ids(self) -> tuple[str, ...]:
        """Return drive IDs in the same order as ``locations``."""
        return tuple(location.drive_id for location in self.locations)


async def list_user_groups(
    user_id: str, source: GroupDriveSource
) -> tuple[UserGroup, ...]:
    """List a user's groups, preserving source order and removing duplicate IDs.

    Args:
        user_id: User whose group memberships are requested.
        source: Port used to stream group memberships.

    Returns:
        Unique groups in the order they first appear in the source stream.

    Raises:
        ValueError: If ``user_id`` is blank.
    """
    if not user_id.strip():
        raise ValueError("user_id is required")
    groups: dict[str, UserGroup] = {}
    async for group in source.stream_user_groups(user_id):
        _ = groups.setdefault(group.id, group)
    return tuple(groups.values())


async def list_user_group_drives(
    user_id: str, source: GroupDriveSource
) -> tuple[GroupDriveCollection, ...]:
    """List accessible drives for each group the user belongs to.

    Duplicate drive IDs are removed, and groups with no drives are omitted.

    Args:
        user_id: User whose memberships determine accessible groups.
        source: Port used to stream groups and their drive locations.

    Returns:
        Group collections in membership order.

    Raises:
        ValueError: If ``user_id`` is blank.
    """
    groups_with_drives: list[GroupDriveCollection] = []
    for group in await list_user_groups(user_id, source):
        locations: dict[str, GroupDriveLocation] = {}
        async for location in source.stream_group_drive_locations(group.id):
            _ = locations.setdefault(location.drive_id, location)
        if locations:
            groups_with_drives.append(
                GroupDriveCollection(group.id, group.name, tuple(locations.values()))
            )
    return tuple(groups_with_drives)


async def list_group_drives(
    user_id: str, group_id: str, source: GroupDriveSource
) -> GroupDriveCollection:
    """List drives for a group after confirming the user belongs to it.

    Args:
        user_id: User whose group memberships are checked.
        group_id: Group whose drive locations are requested.
        source: Port used to stream memberships and drive locations.

    Returns:
        The group and its unique drive locations, including an empty collection
        when the group has no drives.

    Raises:
        ValueError: If ``user_id`` or ``group_id`` is blank.
        GroupDriveNotAccessible: If the user is not a member of the group.
    """
    if not group_id.strip():
        raise ValueError("group_id is required")
    group = next(
        (
            group
            for group in await list_user_groups(user_id, source)
            if group.id == group_id
        ),
        None,
    )
    if group is None:
        raise GroupDriveNotAccessible("Group is not accessible to the caller")
    locations: dict[str, GroupDriveLocation] = {}
    async for location in source.stream_group_drive_locations(group_id):
        _ = locations.setdefault(location.drive_id, location)
    return GroupDriveCollection(group.id, group.name, tuple(locations.values()))
