import asyncio
from collections.abc import AsyncIterator
from typing import override

import pytest

from domain.exceptions.metadata_inventory_errors import MetadataSourceError
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.ports.documents.document_metadata_source import DocumentMetadataItem
from domain.ports.documents.group_drive_source import GroupDriveLocation, UserGroup
from domain.use_cases.group_drive_discovery import GroupNotAccessible
from domain.use_cases.group_metadata_inventory import (
    DriveInventoryFailure,
    DriveMetadataSelector,
    GroupMetadataRequest,
    inventory_group_metadata,
)


class Groups:
    async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        assert user_id == "user"
        yield UserGroup("group", "Research")

    async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        assert group_id == "group"
        yield "team-drive"
        yield "private-drive"

    async def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]:
        async for drive_id in self.stream_group_drive_ids(group_id):
            yield GroupDriveLocation(drive_id)


class Metadata:
    def __init__(self, fail_drive: str | None = None, fail_status: int = 403) -> None:
        self.calls: list[tuple[str, list[str], list[str], list[str], list[str]]] = []
        self.fail_drive: str | None = fail_drive
        self.fail_status: int = fail_status
        self.roots: list[tuple[str, str]] = []

    async def stream_metadata(
        self,
        drive_id: str,
        extensions: list[str],
        include_paths: list[str],
        exclude_paths: list[str],
        exclude_extensions: list[str],
        root_item_id: str,
    ) -> AsyncIterator[DocumentMetadataItem]:
        self.roots.append((drive_id, root_item_id))
        self.calls.append(
            (drive_id, extensions, include_paths, exclude_paths, exclude_extensions)
        )
        if drive_id == self.fail_drive:
            raise MetadataSourceError(self.fail_status, "Drive denied")
        yield DocumentMetadataItem(
            Document(
                _id=DocumentId(drive_id, "item"),
                file_metadata=FileMetadata(
                    name="Report.pdf",
                    mime_type="application/pdf",
                    size_bytes=10,
                    content_version=ContentVersion("v1"),
                    source_url=None,
                ),
                document_metadata=DocumentMetadata(),
            ),
            1,
            1,
        )


class Store:
    def __init__(self) -> None:
        self.saved: list[Document] = []

    async def save_document(self, document: Document) -> None:
        self.saved.append(document)


def test_include_and_exclude_apply_per_drive_filters() -> None:
    metadata, store = Metadata(), Store()
    request = GroupMetadataRequest(
        "group",
        include=[DriveMetadataSelector("private-drive", ["/Reports/"], [".PDF"])],
        exclude=[DriveMetadataSelector("private-drive", ["/Reports/Drafts/"])],
    )

    async def collect():
        return [
            update
            async for update in inventory_group_metadata(
                "user", request, Groups(), metadata, store
            )
        ]

    updates = asyncio.run(collect())
    assert [update.drive_id for update in updates] == ["private-drive"]
    assert metadata.calls == [
        ("private-drive", ["pdf"], ["Reports"], ["Reports/Drafts"], []),
    ]
    assert [document.id.id[0] for document in store.saved] == [
        "private-drive",
    ]


def test_empty_include_uses_all_drives_and_excludes_selected_extensions() -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> list[str]:
        return [
            update.drive_id
            async for update in inventory_group_metadata(
                "user",
                GroupMetadataRequest(
                    "group",
                    exclude=[
                        DriveMetadataSelector("private-drive", extensions=[".DOCX"])
                    ],
                ),
                Groups(),
                metadata,
                store,
            )
        ]

    assert asyncio.run(collect()) == ["team-drive", "private-drive"]
    assert metadata.calls == [
        ("team-drive", [], [], [], []),
        ("private-drive", [], [], [], ["docx"]),
    ]


def test_channel_folder_root_is_passed_to_metadata_scan() -> None:
    class ChannelGroup(Groups):
        @override
        async def stream_group_drive_locations(
            self, group_id: str
        ) -> AsyncIterator[GroupDriveLocation]:
            assert group_id == "group"
            yield GroupDriveLocation("private-drive", "channel-root")

    metadata, store = Metadata(), Store()

    async def collect() -> list[str]:
        return [
            update.drive_id
            async for update in inventory_group_metadata(
                "user", GroupMetadataRequest("group"), ChannelGroup(), metadata, store
            )
        ]

    assert asyncio.run(collect()) == ["private-drive"]
    assert metadata.roots == [("private-drive", "channel-root")]


@pytest.mark.parametrize(
    "selection",
    [
        GroupMetadataRequest("group", exclude=[DriveMetadataSelector("unknown")]),
        GroupMetadataRequest(
            "group",
            include=[DriveMetadataSelector("team-drive", paths=["../bad"])],
        ),
    ],
)
def test_invalid_selection_fails_before_any_write(
    selection: GroupMetadataRequest,
) -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> None:
        async for _ in inventory_group_metadata(
            "user", selection, Groups(), metadata, store
        ):
            pass

    with pytest.raises(ValueError):
        asyncio.run(collect())
    assert metadata.calls == []
    assert store.saved == []


def test_unavailable_explicit_include_fails_before_any_write() -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> None:
        async for _ in inventory_group_metadata(
            "user",
            GroupMetadataRequest("group", include=[DriveMetadataSelector("unknown")]),
            Groups(),
            metadata,
            store,
        ):
            pass

    with pytest.raises(DriveInventoryFailure) as error:
        asyncio.run(collect())
    assert error.value.drive_id == "unknown"
    assert isinstance(error.value.cause, MetadataSourceError)
    assert error.value.cause.status_code == 403
    assert metadata.calls == []
    assert store.saved == []


def test_excluded_drive_is_not_scanned() -> None:
    metadata, store = Metadata(), Store()

    async def collect():
        return [
            update
            async for update in inventory_group_metadata(
                "user",
                GroupMetadataRequest(
                    "group", exclude=[DriveMetadataSelector("team-drive")]
                ),
                Groups(),
                metadata,
                store,
            )
        ]

    assert [update.drive_id for update in asyncio.run(collect())] == ["private-drive"]


def test_inaccessible_drive_is_skipped_and_later_drive_continues() -> None:
    class ThreeDrives(Groups):
        @override
        async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
            assert group_id == "group"
            yield "team-drive"
            yield "private-drive"
            yield "other-drive"

    metadata, store = Metadata("private-drive"), Store()

    async def collect():
        return [
            update
            async for update in inventory_group_metadata(
                "user", GroupMetadataRequest("group"), ThreeDrives(), metadata, store
            )
        ]

    updates = asyncio.run(collect())
    assert [update.drive_id for update in updates] == [
        "team-drive",
        "private-drive",
        "other-drive",
    ]
    assert [document.id.id[0] for document in store.saved] == [
        "team-drive",
        "other-drive",
    ]


def test_explicitly_included_drive_denial_stops_run() -> None:
    metadata, store = Metadata("private-drive"), Store()

    async def collect() -> list[str]:
        return [
            update.drive_id
            async for update in inventory_group_metadata(
                "user",
                GroupMetadataRequest(
                    "group",
                    include=[
                        DriveMetadataSelector("team-drive"),
                        DriveMetadataSelector("private-drive"),
                    ],
                ),
                Groups(),
                metadata,
                store,
            )
        ]

    with pytest.raises(DriveInventoryFailure) as error:
        asyncio.run(collect())
    assert error.value.drive_id == "private-drive"
    assert isinstance(error.value.cause, MetadataSourceError)
    assert error.value.cause.status_code == 403
    assert [document.id.id[0] for document in store.saved] == ["team-drive"]


def test_later_drive_failure_reports_drive_and_keeps_earlier_writes() -> None:
    metadata, store = Metadata("private-drive", 503), Store()
    seen: list[str] = []

    async def collect() -> None:
        async for update in inventory_group_metadata(
            "user", GroupMetadataRequest("group"), Groups(), metadata, store
        ):
            seen.append(update.drive_id)

    with pytest.raises(DriveInventoryFailure) as error:
        asyncio.run(collect())
    assert error.value.drive_id == "private-drive"
    assert isinstance(error.value.cause, MetadataSourceError)
    assert seen == ["team-drive"]
    assert [document.id.id[0] for document in store.saved] == ["team-drive"]


def test_nonmember_group_is_rejected_before_drive_listing() -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> None:
        async for _ in inventory_group_metadata(
            "user", GroupMetadataRequest("other"), Groups(), metadata, store
        ):
            pass

    with pytest.raises(GroupNotAccessible):
        asyncio.run(collect())
    assert metadata.calls == []
