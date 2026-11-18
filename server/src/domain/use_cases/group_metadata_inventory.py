from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import cast

from domain.exceptions.metadata_inventory_errors import MetadataSourceError
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataSource,
    DocumentRefreshSource,
)
from domain.ports.documents.document_store import DocumentRefreshStore, DocumentStore
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import get_group_drives
from domain.use_cases.metadata_inventory import (
    InventoryMetadataProgress,
    InventoryMetadataRequest,
    inventory_metadata,
    refresh_metadata,
)


@dataclass(frozen=True)
class DriveMetadataSelector:
    drive_id: str
    paths: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GroupMetadataRequest:
    group_id: str
    include: list[DriveMetadataSelector] = field(default_factory=list)
    exclude: list[DriveMetadataSelector] = field(default_factory=list)


@dataclass(frozen=True)
class GroupMetadataProgress:
    drive_id: str
    update: InventoryMetadataProgress


@dataclass(frozen=True)
class GroupMetadataSkipped:
    drive_id: str
    reason: str


class DriveInventoryFailure(Exception):
    def __init__(self, drive_id: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.drive_id: str = drive_id
        self.cause: Exception = cause


async def inventory_group_metadata(
    user_id: str,
    request: GroupMetadataRequest,
    drive_source: GroupDriveSource,
    metadata_source: DocumentMetadataSource,
    store: DocumentStore,
) -> AsyncIterator[GroupMetadataProgress | GroupMetadataSkipped]:
    """Validate the full selection, then inventory selected drives in discovery order."""
    group = await get_group_drives(user_id, request.group_id, drive_source)
    locations = {location.drive_id: location for location in group.locations}
    available = set(locations)
    included = {selector.drive_id: selector for selector in request.include}
    excluded = {selector.drive_id: selector for selector in request.exclude}
    if len(included) != len(request.include) or len(excluded) != len(request.exclude):
        raise ValueError("include and exclude must each have unique drive_id values")
    unavailable_included = [
        drive_id for drive_id in included if drive_id not in available
    ]
    if unavailable_included:
        raise DriveInventoryFailure(
            unavailable_included[0],
            MetadataSourceError(403, "Included drive is not accessible in this group"),
        )
    unknown_excluded = set(excluded) - available
    if unknown_excluded:
        raise ValueError(
            "drive IDs are not available in this group: "
            + ", ".join(sorted(unknown_excluded))
        )
    if included and set(excluded) - set(included):
        raise ValueError("excluded drives must also be included")
    selected = tuple(
        drive_id
        for drive_id in group.drive_ids
        if (not included or drive_id in included)
        and (
            drive_id not in excluded
            or excluded[drive_id].paths
            or excluded[drive_id].extensions
        )
    )
    # Construct every per-drive request before the first write.
    requests = [
        InventoryMetadataRequest(
            drive_id,
            included[drive_id].extensions if drive_id in included else [],
            included[drive_id].paths if drive_id in included else [],
            excluded[drive_id].paths if drive_id in excluded else [],
            excluded[drive_id].extensions if drive_id in excluded else [],
            locations[drive_id].root_item_id,
        )
        for drive_id in selected
    ]
    for drive_request in requests:
        try:
            # Retain the legacy port path for existing in-memory adapters.
            # Production Graph/PostgreSQL adapters implement the refresh ports.
            supports_scan = callable(
                getattr(type(metadata_source), "scan_metadata", None)
            )
            supports_run = callable(getattr(type(store), "begin_metadata_run", None))
            if supports_scan != supports_run:
                raise RuntimeError("Metadata source and store refresh ports must agree")
            if supports_scan:
                updates = refresh_metadata(
                    user_id,
                    drive_request,
                    cast(DocumentRefreshSource, cast(object, metadata_source)),
                    cast(DocumentRefreshStore, cast(object, store)),
                )
            else:
                updates = inventory_metadata(drive_request, metadata_source, store)
            async for update in updates:
                yield GroupMetadataProgress(drive_request.drive_id, update)
        except MetadataSourceError as exc:
            if exc.status_code == 403 and not included:
                yield GroupMetadataSkipped(drive_request.drive_id, str(exc))
                continue
            raise DriveInventoryFailure(drive_request.drive_id, exc) from exc
        except Exception as exc:
            raise DriveInventoryFailure(drive_request.drive_id, exc) from exc
