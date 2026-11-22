import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import cast, override
from uuid import UUID, uuid4

import pytest

from api.routes.operations import get_operation_run
from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.metadata_selection import (
    MetadataSelection,
)
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from domain.ports.documents.document_store import (
    MetadataIngestRun,
    MetadataRunPublication,
)
from domain.ports.documents.group_drive_source import GroupDriveLocation, UserGroup
from domain.ports.operation_run_store import OperationRun
from domain.use_cases.group_drive_discovery import GroupNotAccessible
from domain.use_cases.metadata_refresh import (
    DriveMetadataSelector,
    MetadataIngestDriveFailure,
    MetadataIngestProgress,
    MetadataIngestRequest,
    MetadataIngestResult,
    MetadataIngestSkipped,
    MetadataIngestStarted,
)
from domain.use_cases.metadata_refresh.refresh import (
    refresh_group_metadata_atomically,
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
        selection: MetadataSelection,
        root_item_id: str,
        cursor: str | None,
        capture_relative_paths: bool = False,
    ) -> DocumentMetadataScan:
        _ = capture_relative_paths
        assert cursor is None
        self.roots.append((drive_id, root_item_id))
        include_rules = selection.include_rules
        exclude_rules = selection.exclude_rules
        extensions = list(include_rules[0].extensions) if include_rules else []
        include_paths = [rule.path for rule in include_rules if rule.path]
        exclude_paths = [rule.path for rule in exclude_rules if rule.path]
        exclude_extensions = (
            list(exclude_rules[0].extensions)
            if exclude_rules and exclude_rules[0].extensions
            else []
        )
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
        self.staged: dict[UUID, list[Document]] = {}
        self.published_batches: list[list[UUID]] = []
        self.operation_id: UUID | None = None
        self.operation_status: str | None = None
        self.operation_failure_code: str | None = None
        self.operation_summary: dict[str, object] = {}
        self.operation_domain_ids: dict[str, object] = {}
        self.operation_principal_id: str | None = None
        self.operation_started_at: datetime | None = None
        self.operation_finished_at: datetime | None = None
        self.metadata_runs_started: int = 0
        self.selections: list[MetadataSelection] = []

    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
        assert principal_id == "user"
        _ = group_id
        self.operation_id = uuid4()
        self.operation_status = "running"
        self.operation_principal_id = principal_id
        self.operation_started_at = datetime.now(UTC)
        self.operation_finished_at = None
        self.operation_domain_ids = {"group_id": group_id, "drive_run_ids": []}
        return self.operation_id

    async def begin_metadata_run(
        self,
        principal_id: str,
        drive_id: str,
        selection: MetadataSelection,
        operation_id: UUID | None = None,
    ) -> MetadataIngestRun:
        _ = (principal_id, drive_id)
        self.metadata_runs_started += 1
        self.selections.append(selection)
        if operation_id is not None:
            assert operation_id == self.operation_id
        run_id = uuid4()
        if operation_id is not None:
            self.operation_domain_ids.setdefault("drive_run_ids", [])
            cast(list[str], self.operation_domain_ids["drive_run_ids"]).append(
                str(run_id)
            )
        return MetadataIngestRun(run_id, None, True)

    async def stage_document(self, run_id: UUID, document: Document) -> None:
        self.staged.setdefault(run_id, []).append(document)

    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
        _ = item_id
        self.staged.setdefault(run_id, [])

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        _ = (delta_link, baseline, reconcile)
        self.saved.extend(self.staged.pop(run_id, []))

    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None:
        assert operation_id == self.operation_id
        self.operation_status = "complete"
        self.operation_finished_at = datetime.now(UTC)
        self.operation_summary = summary or {}
        self.operation_domain_ids.update(domain_ids or {})
        run_ids = [publication.run_id for publication in publications]
        self.published_batches.append(run_ids)
        for run_id in run_ids:
            self.saved.extend(self.staged.pop(run_id, []))

    async def fail_group_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        *,
        interrupted: bool = False,
        summary: dict[str, object] | None = None,
    ) -> None:
        assert operation_id == self.operation_id
        self.operation_status = "interrupted" if interrupted else "failed"
        self.operation_failure_code = failure_code
        self.operation_summary.update(summary or {})
        self.operation_finished_at = datetime.now(UTC)

    async def get_operation_run(
        self, operation_id: UUID, principal_id: str
    ) -> OperationRun | None:
        if (
            operation_id != self.operation_id
            or principal_id != self.operation_principal_id
            or self.operation_status is None
            or self.operation_started_at is None
        ):
            return None
        return OperationRun(
            operation_id,
            "metadata.ingest",
            self.operation_status,
            self.operation_started_at,
            self.operation_finished_at,
            self.operation_summary,
            self.operation_failure_code,
            self.operation_domain_ids,
        )

    async def discard_metadata_run(self, run_id: UUID) -> None:
        self.staged.pop(run_id, None)


def _drive_ids(updates: Sequence[object]) -> list[str]:
    return [
        update.drive_id
        for update in updates
        if isinstance(update, (MetadataIngestProgress, MetadataIngestSkipped))
    ]


def test_unavailable_explicit_include_fails_before_any_write() -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> None:
        async for _ in refresh_group_metadata_atomically(
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
            async for update in refresh_group_metadata_atomically(
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
            async for update in refresh_group_metadata_atomically(
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
    assert len(store.published_batches) == 1
    assert len(store.published_batches[0]) == 2


def test_explicitly_included_drive_denial_stops_run() -> None:
    metadata, store = Metadata("private-drive"), Store()

    async def collect() -> list[str]:
        return [
            update.drive_id
            async for update in refresh_group_metadata_atomically(
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
    assert store.saved == []
    assert store.staged == {}


def test_unavailable_explicit_include_is_inspectable_in_operation_status() -> None:
    metadata, store = Metadata(), Store()

    async def run() -> MetadataIngestStarted:
        updates = refresh_group_metadata_atomically(
            "user",
            MetadataIngestRequest(
                "group", include=[DriveMetadataSelector("unlisted-drive")]
            ),
            Groups(),
            metadata,
            store,
        )
        started = await anext(updates)
        assert isinstance(started, MetadataIngestStarted)
        with pytest.raises(MetadataIngestDriveFailure) as error:
            await anext(updates)
        assert error.value.drive_id == "unlisted-drive"
        assert isinstance(error.value.cause, MetadataSourceError)
        assert error.value.cause.status_code == 403
        return started

    started = asyncio.run(run())
    operation = asyncio.run(get_operation_run(started.operation_id, "user", store))

    assert operation.status == "failed"
    assert operation.failure_code == "metadata.source_error"
    assert operation.summary == {
        "processed": 0,
        "skipped_drive_ids": [],
        "failed_drive_id": "unlisted-drive",
        "source_status": 403,
        "reason": "Drive is not accessible",
    }
    assert operation.domain_ids == {"group_id": "group", "drive_run_ids": []}
    assert store.metadata_runs_started == 0
    assert metadata.calls == []
    assert store.operation_status == "failed"
    assert store.published_batches == []


def test_cancellation_before_group_completion_discards_all_staged_drives() -> None:
    metadata, store = Metadata(), Store()

    async def cancel_after_first_progress() -> None:
        updates = refresh_group_metadata_atomically(
            "user", MetadataIngestRequest("group"), Groups(), metadata, store
        )
        started = await anext(updates)
        assert isinstance(started, MetadataIngestStarted)
        first = await anext(updates)
        assert isinstance(first, MetadataIngestProgress)
        await updates.aclose()

    asyncio.run(cancel_after_first_progress())

    assert store.saved == []
    assert store.staged == {}
    assert store.published_batches == []
    assert store.operation_status == "interrupted"


def test_later_drive_failure_discards_all_staged_drives() -> None:
    metadata, store = Metadata("private-drive", 403), Store()
    seen: list[str] = []
    operation_ids: list[UUID] = []

    async def collect() -> None:
        async for update in refresh_group_metadata_atomically(
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
        ):
            if isinstance(update, MetadataIngestStarted):
                operation_ids.append(update.operation_id)
            elif isinstance(update, MetadataIngestProgress | MetadataIngestSkipped):
                seen.append(update.drive_id)

    with pytest.raises(MetadataIngestDriveFailure) as error:
        asyncio.run(collect())
    assert error.value.drive_id == "private-drive"
    assert isinstance(error.value.cause, MetadataSourceError)
    assert seen == ["team-drive"]
    assert store.saved == []
    assert store.staged == {}
    assert store.published_batches == []
    assert len(operation_ids) == 1
    operation = asyncio.run(get_operation_run(operation_ids[0], "user", store))
    assert operation is not None
    assert operation.status == "failed"
    assert operation.failure_code == "metadata.source_error"
    assert operation.summary == {
        "processed": 1,
        "skipped_drive_ids": [],
        "failed_drive_id": "private-drive",
        "source_status": 403,
        "reason": "Drive is not accessible",
    }
    assert operation.domain_ids["group_id"] == "group"
    assert len(cast(list[str], operation.domain_ids["drive_run_ids"])) == 2


def test_nonmember_group_is_rejected_before_drive_listing() -> None:
    metadata, store = Metadata(), Store()

    async def collect() -> None:
        async for _ in refresh_group_metadata_atomically(
            "user", MetadataIngestRequest("other"), Groups(), metadata, store
        ):
            pass

    with pytest.raises(GroupNotAccessible):
        asyncio.run(collect())
    assert metadata.calls == []
