import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import override
from uuid import UUID, uuid4

import pytest

from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from domain.ports.documents.document_store import MetadataIngestRun
from domain.ports.documents.group_drive_source import GroupDriveLocation, UserGroup
from domain.use_cases.group_drive_discovery import GroupNotAccessible
from domain.use_cases.metadata_ingest import (
    DriveMetadataSelector,
    MetadataIngestDriveFailure,
    MetadataIngestProgress,
    MetadataIngestRequest,
    MetadataIngestResult,
    MetadataIngestSkipped,
    ingest_group_metadata,
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

    async def scan_metadata(
        self,
        drive_id: str,
        extensions: list[str],
        include_paths: list[str],
        exclude_paths: list[str],
        exclude_extensions: list[str],
        root_item_id: str,
        cursor: str | None,
    ) -> DocumentMetadataScan:
        assert cursor is None
        self.roots.append((drive_id, root_item_id))
        self.calls.append(
            (drive_id, extensions, include_paths, exclude_paths, exclude_extensions)
        )
        if drive_id == self.fail_drive:
            raise MetadataSourceError(self.fail_status, "Drive denied")
        return DocumentMetadataScan(
            (
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
            ),
            (),
            f"cursor:{drive_id}",
            False,
        )


class Store:
    def __init__(self) -> None:
        self.saved: list[Document] = []
        self.staged: list[Document] = []

    async def start_metadata_run(
        self, principal_id: str, drive_id: str, selection: MetadataSelection
    ) -> MetadataIngestRun:
        _ = (principal_id, drive_id, selection)
        return MetadataIngestRun(uuid4(), None, True)

    async def stage_document(self, run_id: UUID, document: Document) -> None:
        _ = run_id
        self.staged.append(document)

    async def stage_deleted(self, run_id: UUID, item_id: str) -> None:
        _ = (run_id, item_id)

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        _ = (run_id, delta_link, baseline, reconcile)
        self.saved.extend(self.staged)
        self.staged.clear()

    async def fail_metadata_run(self, run_id: UUID) -> None:
        _ = run_id
        self.staged.clear()


def _drive_ids(updates: Sequence[object]) -> list[str]:
    return [
        update.drive_id
        for update in updates
        if isinstance(update, (MetadataIngestProgress, MetadataIngestSkipped))
    ]


def test_include_and_exclude_apply_per_drive_filters() -> None:
    metadata, store = Metadata(), Store()
    request = MetadataIngestRequest(
        "group",
        include=[DriveMetadataSelector("private-drive", ["/Reports/"], [".PDF"])],
        exclude=[DriveMetadataSelector("private-drive", ["/Reports/Drafts/"])],
    )

    async def collect():
        return [
            update
            async for update in ingest_group_metadata(
                "user", request, Groups(), metadata, store
            )
        ]

    updates = asyncio.run(collect())
    assert _drive_ids(updates) == ["private-drive"]
    assert isinstance(updates[-1], MetadataIngestResult)
    assert updates[-1].processed == 1
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
            async for update in ingest_group_metadata(
                "user",
                MetadataIngestRequest(
                    "group",
                    exclude=[
                        DriveMetadataSelector("private-drive", extensions=[".DOCX"])
                    ],
                ),
                Groups(),
                metadata,
                store,
            )
            if isinstance(update, MetadataIngestProgress)
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
            async for update in ingest_group_metadata(
                "user", MetadataIngestRequest("group"), ChannelGroup(), metadata, store
            )
            if isinstance(update, MetadataIngestProgress)
        ]

    assert asyncio.run(collect()) == ["private-drive"]
    assert metadata.roots == [("private-drive", "channel-root")]


@pytest.mark.parametrize(
    "selection",
    [
        MetadataIngestRequest("group", exclude=[DriveMetadataSelector("unknown")]),
        MetadataIngestRequest(
            "group",
            include=[DriveMetadataSelector("team-drive", paths=["../bad"])],
        ),
    ],
)
def test_invalid_selection_fails_before_any_write(
    selection: MetadataIngestRequest,
) -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> None:
        async for _ in ingest_group_metadata(
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
        async for _ in ingest_group_metadata(
            "user",
            MetadataIngestRequest("group", include=[DriveMetadataSelector("unknown")]),
            Groups(),
            metadata,
            store,
        ):
            pass

    with pytest.raises(MetadataIngestDriveFailure) as error:
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
            async for update in ingest_group_metadata(
                "user",
                MetadataIngestRequest(
                    "group", exclude=[DriveMetadataSelector("team-drive")]
                ),
                Groups(),
                metadata,
                store,
            )
        ]

    assert _drive_ids(asyncio.run(collect())) == ["private-drive"]


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
            async for update in ingest_group_metadata(
                "user", MetadataIngestRequest("group"), ThreeDrives(), metadata, store
            )
        ]

    updates = asyncio.run(collect())
    assert _drive_ids(updates) == [
        "team-drive",
        "private-drive",
        "other-drive",
    ]
    assert [
        (update.drive_id, update.processed)
        for update in updates
        if isinstance(update, (MetadataIngestProgress, MetadataIngestSkipped))
    ] == [
        ("team-drive", 1),
        ("private-drive", 1),
        ("other-drive", 2),
    ]
    assert isinstance(updates[-1], MetadataIngestResult)
    assert updates[-1].processed == 2
    assert updates[-1].skipped_drive_ids == ("private-drive",)
    assert [document.id.id[0] for document in store.saved] == [
        "team-drive",
        "other-drive",
    ]


def test_explicitly_included_drive_denial_stops_run() -> None:
    metadata, store = Metadata("private-drive"), Store()

    async def collect() -> list[str]:
        return [
            update.drive_id
            async for update in ingest_group_metadata(
                "user",
                MetadataIngestRequest(
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
            if isinstance(update, MetadataIngestProgress)
        ]

    with pytest.raises(MetadataIngestDriveFailure) as error:
        asyncio.run(collect())
    assert error.value.drive_id == "private-drive"
    assert isinstance(error.value.cause, MetadataSourceError)
    assert error.value.cause.status_code == 403
    assert [document.id.id[0] for document in store.saved] == ["team-drive"]


def test_later_drive_failure_reports_drive_and_keeps_earlier_writes() -> None:
    metadata, store = Metadata("private-drive", 503), Store()
    seen: list[str] = []

    async def collect() -> None:
        async for update in ingest_group_metadata(
            "user", MetadataIngestRequest("group"), Groups(), metadata, store
        ):
            if isinstance(update, MetadataIngestProgress | MetadataIngestSkipped):
                seen.append(update.drive_id)

    with pytest.raises(MetadataIngestDriveFailure) as error:
        asyncio.run(collect())
    assert error.value.drive_id == "private-drive"
    assert isinstance(error.value.cause, MetadataSourceError)
    assert seen == ["team-drive"]
    assert [document.id.id[0] for document in store.saved] == ["team-drive"]


def test_nonmember_group_is_rejected_before_drive_listing() -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> None:
        async for _ in ingest_group_metadata(
            "user", MetadataIngestRequest("other"), Groups(), metadata, store
        ):
            pass

    with pytest.raises(GroupNotAccessible):
        asyncio.run(collect())
    assert metadata.calls == []
