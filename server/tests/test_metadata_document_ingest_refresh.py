import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast, final, override
from uuid import UUID, uuid4

import anyio
import pytest

from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataSourceError,
)
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.document_ingestion import (
    DocumentIngestFileSelection,
    DocumentIngestRequest,
    DocumentProcessingCandidate,
    DocumentProcessingState,
    IngestionDriveSelector,
    ProcessingOutcome,
    ProcessingStatus,
)
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from domain.ports.documents.document_store import (
    MetadataIngestRun,
    MetadataRunPublication,
)
from domain.ports.documents.group_drive_source import GroupDriveLocation, UserGroup
from domain.ports.operation_run_store import OperationRun
from domain.use_cases.metadata_ingest import (
    MetadataDriveFilesSelected,
    MetadataDrivePublished,
    MetadataDriveRefreshFailure,
    MetadataIngestProgress,
    MetadataIngestRequest,
    MetadataIngestStarted,
    MetadataRefreshRunResult,
    ingest_group_document_candidates,
    refresh_group_metadata_for_document_ingest,
)


@final
class ThreeDriveGroups:
    def __init__(self, drive_ids: tuple[str, ...] = ("first", "second")) -> None:
        self.drive_ids: tuple[str, ...] = drive_ids

    async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        assert user_id == "user"
        yield UserGroup("group", "Research")

    async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        assert group_id == "group"
        for drive_id in self.drive_ids:
            yield drive_id

    async def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]:
        async for drive_id in self.stream_group_drive_ids(group_id):
            yield GroupDriveLocation(drive_id)


@final
class MetadataSource:
    def __init__(self, failures: dict[str, int] | None = None) -> None:
        self.failures: dict[str, int] = failures or {}
        self.calls: list[str] = []
        self.selections: dict[str, MetadataSelection] = {}
        self.relative_path_capture: dict[str, bool] = {}

    async def scan_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str,
        cursor: str | None,
        capture_relative_paths: bool = False,
    ) -> DocumentMetadataScan:
        _ = (selection, root_item_id)
        self.selections[drive_id] = selection
        self.relative_path_capture[drive_id] = capture_relative_paths
        assert cursor is None
        await anyio.lowlevel.checkpoint()
        self.calls.append(drive_id)
        if drive_id in self.failures:
            raise MetadataSourceError(self.failures[drive_id], "Graph request failed")
        return DocumentMetadataScan(
            (document(drive_id),), (), f"cursor:{drive_id}", True
        )


def document(drive_id: str) -> Document:
    return Document(
        _id=DocumentId(drive_id, "item"),
        file_metadata=FileMetadata(
            name="Report.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            content_version=ContentVersion("v1"),
            source_url=None,
        ),
        document_metadata=DocumentMetadata(),
    )


class CombinedStore:
    def __init__(
        self,
        *,
        fail_stage_drive: str | None = None,
        fail_publish_drive: str | None = None,
        commit_then_raise_drive: str | None = None,
    ) -> None:
        self.fail_stage_drive: str | None = fail_stage_drive
        self.fail_publish_drive: str | None = fail_publish_drive
        self.commit_then_raise_drive: str | None = commit_then_raise_drive
        self.operation_id: UUID | None = None
        self.operation_status: str | None = None
        self.operation_failure_code: str | None = None
        self.operation_summary: dict[str, object] = {}
        self.operation_domain_ids: dict[str, object] = {}
        self.operation_started_at: datetime | None = None
        self.operation_finished_at: datetime | None = None
        self.run_drives: dict[UUID, str] = {}
        self.staged: dict[UUID, list[Document]] = {}
        self.published: list[Document] = []
        self.published_drive_ids: list[str] = []
        self.discarded_run_ids: list[UUID] = []
        self.publish_started: asyncio.Event = asyncio.Event()
        self.drive_outcomes: dict[str, dict[str, object]] = {}
        self.cancel_scope_on_publish: anyio.CancelScope | None = None
        self.candidates: dict[str, tuple[DocumentProcessingCandidate, ...]] = {}
        self.candidate_queries: list[tuple[str, str, UUID]] = []
        self.file_selections: list[tuple[UUID, tuple[DocumentIngestFileSelection, ...]]] = []

    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
        assert (principal_id, group_id) == ("user", "group")
        self.operation_id = uuid4()
        self.operation_status = "running"
        self.operation_started_at = datetime.now(UTC)
        self.operation_summary = {"drive_outcomes": {}}
        self.operation_domain_ids = {"group_id": group_id, "drive_run_ids": []}
        return self.operation_id

    async def begin_metadata_run(
        self,
        principal_id: str,
        drive_id: str,
        selection: MetadataSelection,
        operation_id: UUID | None = None,
    ) -> MetadataIngestRun:
        _ = (principal_id, selection)
        assert operation_id == self.operation_id
        run_id = uuid4()
        self.run_drives[run_id] = drive_id
        self.operation_domain_ids.setdefault("drive_run_ids", [])
        cast(list[str], self.operation_domain_ids["drive_run_ids"]).append(
            str(run_id)
        )
        return MetadataIngestRun(run_id, None, True)

    async def stage_document(self, run_id: UUID, document: Document) -> None:
        self.staged.setdefault(run_id, []).append(document)
        if self.run_drives[run_id] == self.fail_stage_drive:
            raise RuntimeError("staging failed")

    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
        _ = item_id
        self.staged.setdefault(run_id, [])

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        _ = (run_id, delta_link, baseline, reconcile)
        raise AssertionError("combined refresh must publish one drive at a time")

    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None:
        _ = (publications, operation_id, summary, domain_ids)
        raise AssertionError("combined refresh must not use group-atomic publication")

    async def publish_metadata_drive(
        self,
        operation_id: UUID,
        drive_id: str,
        publication: MetadataRunPublication,
        outcome: dict[str, object],
    ) -> None:
        assert operation_id == self.operation_id
        self.publish_started.set()
        if drive_id == self.fail_publish_drive:
            raise RuntimeError("publication failed")
        self.published.extend(self.staged.pop(publication.run_id, []))
        self.published_drive_ids.append(drive_id)
        self.drive_outcomes[drive_id] = outcome
        self.operation_summary["drive_outcomes"] = dict(self.drive_outcomes)
        if drive_id == self.commit_then_raise_drive:
            raise RuntimeError("publication acknowledgement lost")
        if self.cancel_scope_on_publish is not None:
            self.cancel_scope_on_publish.cancel()

    async def record_metadata_drive_outcome(
        self, operation_id: UUID, drive_id: str, outcome: dict[str, object]
    ) -> None:
        assert operation_id == self.operation_id
        existing = self.drive_outcomes.get(drive_id)
        if existing is not None and existing.get("status") in {
            "published",
            "failed",
            "skipped",
            "interrupted",
        }:
            return
        self.drive_outcomes[drive_id] = outcome
        self.operation_summary["drive_outcomes"] = dict(self.drive_outcomes)

    async def complete_group_operation(
        self, operation_id: UUID, summary: dict[str, object]
    ) -> None:
        assert operation_id == self.operation_id
        self.operation_summary.update(summary)
        self.operation_status = "complete"
        self.operation_finished_at = datetime.now(UTC)

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

    async def discard_metadata_run(self, run_id: UUID) -> None:
        self.staged.pop(run_id, None)
        self.discarded_run_ids.append(run_id)

    async def query_visible_document_candidates(
        self, principal_id: str, drive_id: str, published_run_id: UUID
    ) -> tuple[DocumentProcessingCandidate, ...]:
        assert principal_id == "user"
        self.candidate_queries.append((principal_id, drive_id, published_run_id))
        return self.candidates.get(drive_id, ())

    async def record_document_ingest_selections(
        self,
        operation_id: UUID,
        selections: tuple[DocumentIngestFileSelection, ...],
    ) -> None:
        assert operation_id == self.operation_id
        self.file_selections.append((operation_id, selections))

    async def record_document_processing_outcome(
        self,
        drive_id: str,
        item_id: str,
        source_content_version: str | None,
        processing_policy_version: str,
        outcome: ProcessingOutcome | str,
        *,
        deferred: bool = False,
        failure_stage: str | None = None,
        failure_detail: str | None = None,
    ) -> None:
        _ = (
            drive_id,
            item_id,
            source_content_version,
            processing_policy_version,
            outcome,
            deferred,
            failure_stage,
            failure_detail,
        )

    async def get_operation_run(
        self, operation_id: UUID, principal_id: str
    ) -> OperationRun | None:
        if (
            operation_id != self.operation_id
            or principal_id != "user"
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


@final
class CandidateStoreFailure(CombinedStore):
    def __init__(self, failure_stage: str) -> None:
        super().__init__()
        self.failure_stage = failure_stage

    @override
    async def query_visible_document_candidates(
        self, principal_id: str, drive_id: str, published_run_id: UUID
    ) -> tuple[DocumentProcessingCandidate, ...]:
        if self.failure_stage == "query":
            raise RuntimeError("candidate query failed")
        if self.failure_stage == "stale":
            raise ValueError("Published metadata snapshot is no longer current")
        return await super().query_visible_document_candidates(
            principal_id, drive_id, published_run_id
        )

    @override
    async def record_document_ingest_selections(
        self,
        operation_id: UUID,
        selections: tuple[DocumentIngestFileSelection, ...],
    ) -> None:
        if self.failure_stage == "record":
            raise RuntimeError("selection persistence failed")
        await super().record_document_ingest_selections(operation_id, selections)


def test_combined_refresh_publishes_success_and_records_partial_drive_outcomes() -> None:
    groups = ThreeDriveGroups(("first", "failed", "denied"))
    source = MetadataSource({"denied": 403})
    store = CombinedStore(fail_stage_drive="failed")

    async def collect() -> list[object]:
        return [
            event
            async for event in refresh_group_metadata_for_document_ingest(
                "user", MetadataIngestRequest("group"), groups, source, store
            )
        ]

    events = asyncio.run(collect())

    assert isinstance(events[0], MetadataIngestStarted)
    published = [event for event in events if isinstance(event, MetadataDrivePublished)]
    failures = [
        event for event in events if isinstance(event, MetadataDriveRefreshFailure)
    ]
    assert [event.drive_id for event in published] == ["first"]
    assert [(event.drive_id, event.outcome_status) for event in failures] == [
        ("failed", "failed"),
        ("denied", "skipped"),
    ]
    assert isinstance(events[-1], MetadataRefreshRunResult)
    assert events[-1].processed == 1
    assert events[-1].failed_drive_ids == ("failed",)
    assert events[-1].skipped_drive_ids == ("denied",)
    assert store.published_drive_ids == ["first"]
    assert [value.id.id[0] for value in store.published] == ["first"]
    assert store.staged == {}
    assert store.discarded_run_ids
    assert store.operation_status == "complete"
    assert {
        drive_id: outcome["status"]
        for drive_id, outcome in store.drive_outcomes.items()
    } == {"first": "published", "failed": "failed", "denied": "skipped"}
    assert store.operation_summary["failed_drive_ids"] == ["failed"]
    assert store.operation_summary["skipped_drive_ids"] == ["denied"]


def test_document_candidates_are_selected_only_after_each_drive_publishes() -> None:
    groups = ThreeDriveGroups(("first", "failed"))
    source = MetadataSource({"failed": 500})
    store = CombinedStore()
    store.candidates = {
        "first": (
            DocumentProcessingCandidate(
                "first",
                "visible-item",
                "Report.pdf",
                "reports/Report.pdf",
                "v1",
                None,
            ),
        ),
        "failed": (
            DocumentProcessingCandidate(
                "failed",
                "not-eligible",
                "Failed.pdf",
                "reports/Failed.pdf",
                "v1",
                None,
            ),
        ),
    }
    request = DocumentIngestRequest("group", "policy-v1")

    async def collect() -> list[object]:
        return [
            event
            async for event in ingest_group_document_candidates(
                "user", request, groups, source, store
            )
        ]

    events = asyncio.run(collect())

    assert [drive for _, drive, _ in store.candidate_queries] == ["first"]
    assert len(store.file_selections) == 1
    operation_id, selections = store.file_selections[0]
    assert operation_id == store.operation_id
    assert [(selection.drive_id, selection.item_id) for selection in selections] == [
        ("first", "visible-item")
    ]
    assert selections[0].statuses == (ProcessingStatus.NEW,)
    selected_events = [
        event for event in events if isinstance(event, MetadataDriveFilesSelected)
    ]
    assert [(event.drive_id, event.selected_count) for event in selected_events] == [
        ("first", 1)
    ]


def test_document_exclusions_scope_metadata_refresh_and_status_selection() -> None:
    groups = ThreeDriveGroups(("whole", "path-filtered", "status-only"))
    source = MetadataSource()
    store = CombinedStore()
    store.candidates = {
        "path-filtered": (
            DocumentProcessingCandidate(
                "path-filtered", "pdf", "Report.pdf", "reports/Report.pdf", "v1", None
            ),
        ),
        "status-only": (
            DocumentProcessingCandidate(
                "status-only",
                "failed",
                "Report.pdf",
                "reports/Report.pdf",
                "v1",
                DocumentProcessingState(
                    last_attempt_content_version="v1",
                    last_attempt_policy_version="policy-v1",
                    last_outcome=ProcessingOutcome.FAILED,
                ),
            ),
        ),
    }
    request = DocumentIngestRequest(
        "group",
        "policy-v1",
        exclude=(
            IngestionDriveSelector(drive_id="whole"),
            IngestionDriveSelector(
                drive_id="path-filtered",
                paths=("reports",),
                extensions=("pdf",),
            ),
            IngestionDriveSelector(
                drive_id="status-only", statuses=(ProcessingStatus.FAILED,)
            ),
        ),
    )

    async def collect() -> list[object]:
        return [
            event
            async for event in ingest_group_document_candidates(
                "user", request, groups, source, store
            )
        ]

    asyncio.run(collect())

    assert source.calls == ["path-filtered", "status-only"]
    assert not source.selections["path-filtered"].includes("reports/Report.pdf", "pdf")
    assert source.selections["path-filtered"].includes("reports/Report.md", "md")
    assert source.selections["status-only"].includes("reports/Report.pdf", "pdf")
    assert [drive for _, drive, _ in store.candidate_queries] == [
        "path-filtered",
        "status-only",
    ]
    assert [selections for _, selections in store.file_selections] == [(), ()]


@pytest.mark.parametrize(
    ("drive_ids", "include", "exclude_drive"),
    [
        (("first",), (), "unknown"),
        (
            ("first", "second"),
            (IngestionDriveSelector(drive_id="first"),),
            "second",
        ),
    ],
)
def test_status_only_exclude_drive_scope_is_validated_before_metadata_writes(
    drive_ids: tuple[str, ...],
    include: tuple[IngestionDriveSelector, ...],
    exclude_drive: str,
) -> None:
    groups = ThreeDriveGroups(drive_ids)
    source = MetadataSource()
    store = CombinedStore()
    request = DocumentIngestRequest(
        "group",
        "policy-v1",
        include=include,
        exclude=(
            IngestionDriveSelector(
                drive_id=exclude_drive, statuses=(ProcessingStatus.FAILED,)
            ),
        ),
    )

    async def collect() -> None:
        async for _event in ingest_group_document_candidates(
            "user", request, groups, source, store
        ):
            pass

    with pytest.raises(InvalidMetadataSelection):
        asyncio.run(collect())

    assert source.calls == []
    assert store.published == []
    assert store.staged == {}


def test_path_scoped_status_exclusion_captures_paths_without_narrowing_refresh() -> None:
    groups = ThreeDriveGroups(("drive",))
    source = MetadataSource()
    store = CombinedStore()
    store.candidates = {
        "drive": (
            DocumentProcessingCandidate(
                "drive",
                "other-path",
                "Report.pdf",
                "Elsewhere/Report.pdf",
                "v1",
                None,
            ),
        )
    }
    request = DocumentIngestRequest(
        "group",
        "policy-v1",
        exclude=(
            IngestionDriveSelector(
                drive_id="drive",
                paths=("Reports",),
                statuses=(ProcessingStatus.FAILED,),
            ),
        ),
    )

    async def collect() -> list[object]:
        return [
            event
            async for event in ingest_group_document_candidates(
                "user", request, groups, source, store
            )
        ]

    events = asyncio.run(collect())

    assert source.calls == ["drive"]
    assert source.relative_path_capture == {"drive": True}
    assert source.selections["drive"].includes("Elsewhere/Report.pdf", "pdf")
    assert len(store.file_selections) == 1
    assert [selection.item_id for selection in store.file_selections[0][1]] == [
        "other-path"
    ]
    assert any(
        isinstance(event, MetadataDriveFilesSelected) and event.selected_count == 1
        for event in events
    )


def test_empty_include_keeps_other_drives_in_scope_for_path_status_exclusion() -> None:
    groups = ThreeDriveGroups(("drive-a", "drive-b"))
    source = MetadataSource()
    store = CombinedStore()
    store.candidates = {
        drive_id: (
            DocumentProcessingCandidate(
                drive_id,
                f"{drive_id}-item",
                "Report.pdf",
                "Elsewhere/Report.pdf",
                "v1",
                None,
            ),
        )
        for drive_id in ("drive-a", "drive-b")
    }
    request = DocumentIngestRequest(
        "group",
        "policy-v1",
        exclude=(
            IngestionDriveSelector(
                drive_id="drive-a",
                paths=("Reports",),
                statuses=(ProcessingStatus.FAILED,),
            ),
        ),
    )

    async def collect() -> list[object]:
        return [
            event
            async for event in ingest_group_document_candidates(
                "user", request, groups, source, store
            )
        ]

    events = asyncio.run(collect())

    assert source.calls == ["drive-a", "drive-b"]
    assert source.relative_path_capture == {"drive-a": True, "drive-b": False}
    assert [drive for _, drive, _ in store.candidate_queries] == [
        "drive-a",
        "drive-b",
    ]
    assert [
        [selection.drive_id for selection in selections]
        for _, selections in store.file_selections
    ] == [["drive-a"], ["drive-b"]]
    assert [
        (event.drive_id, event.selected_count)
        for event in events
        if isinstance(event, MetadataDriveFilesSelected)
    ] == [("drive-a", 1), ("drive-b", 1)]


@pytest.mark.parametrize(
    ("failure_stage", "expected_error"),
    [("query", RuntimeError), ("stale", ValueError), ("record", RuntimeError)],
)
def test_candidate_stage_failure_keeps_published_drive_outcome(
    failure_stage: str, expected_error: type[Exception]
) -> None:
    store = CandidateStoreFailure(failure_stage)
    store.candidates = {
        "first": (
            DocumentProcessingCandidate(
                "first", "item", "Report.pdf", "Report.pdf", "v1", None
            ),
        )
    }
    groups = ThreeDriveGroups(("first",))
    request = DocumentIngestRequest("group", "policy-v1")
    seen_events: list[object] = []

    async def collect() -> None:
        async for event in ingest_group_document_candidates(
            "user", request, groups, MetadataSource(), store
        ):
            seen_events.append(event)

    with pytest.raises(expected_error):
        asyncio.run(collect())

    assert store.operation_status == "failed"
    assert store.operation_failure_code == "document_ingest.selection_failed"
    assert store.drive_outcomes["first"]["status"] == "published"
    assert not any(isinstance(event, MetadataDriveFilesSelected) for event in seen_events)


def test_cancellation_keeps_published_drive_and_marks_active_drive_interrupted() -> None:
    groups = ThreeDriveGroups(("first", "second"))
    source = MetadataSource()
    store = CombinedStore()

    async def cancel_during_second_drive() -> None:
        updates = refresh_group_metadata_for_document_ingest(
            "user", MetadataIngestRequest("group"), groups, source, store
        )
        assert isinstance(await anext(updates), MetadataIngestStarted)
        while True:
            event = await anext(updates)
            if isinstance(event, MetadataIngestProgress) and event.drive_id == "second":
                await updates.aclose()
                break

    asyncio.run(cancel_during_second_drive())

    assert store.published_drive_ids == ["first"]
    assert [value.id.id[0] for value in store.published] == ["first"]
    assert store.staged == {}
    assert store.operation_status == "interrupted"
    assert store.drive_outcomes["first"]["status"] == "published"
    assert store.drive_outcomes["second"]["status"] == "interrupted"


def test_cancellation_after_publish_preserves_published_drive_outcome() -> None:
    groups = ThreeDriveGroups(("first", "second"))
    source = MetadataSource()
    store = CombinedStore()

    async def close_after_publication() -> None:
        updates = refresh_group_metadata_for_document_ingest(
            "user", MetadataIngestRequest("group"), groups, source, store
        )
        assert isinstance(await anext(updates), MetadataIngestStarted)
        while True:
            event = await anext(updates)
            if isinstance(event, MetadataDrivePublished):
                assert event.drive_id == "first"
                await updates.aclose()
                break

    asyncio.run(close_after_publication())

    assert store.published_drive_ids == ["first"]
    assert store.operation_status == "interrupted"
    assert store.drive_outcomes["first"]["status"] == "published"
    assert "drive_outcomes" in store.operation_summary


def test_pending_cancellation_at_publish_boundary_preserves_published_outcome() -> None:
    groups = ThreeDriveGroups(("first", "second"))
    source = MetadataSource()
    store = CombinedStore()

    async def cancel_at_publish_boundary() -> None:
        updates = refresh_group_metadata_for_document_ingest(
            "user", MetadataIngestRequest("group"), groups, source, store
        )
        try:
            with anyio.CancelScope() as cancel_scope:
                store.cancel_scope_on_publish = cancel_scope
                assert isinstance(await anext(updates), MetadataIngestStarted)
                try:
                    async for _ in updates:
                        pass
                except anyio.get_cancelled_exc_class():
                    pass
        finally:
            await updates.aclose()

    asyncio.run(cancel_at_publish_boundary())

    assert store.published_drive_ids == ["first"]
    assert [value.id.id[0] for value in store.published] == ["first"]
    assert store.operation_status == "interrupted"
    assert store.drive_outcomes["first"]["status"] == "published"


def test_publish_failure_discards_prepared_drive_and_preserves_prior_publication() -> None:
    groups = ThreeDriveGroups(("first", "second"))
    source = MetadataSource()
    store = CombinedStore(fail_publish_drive="second")

    async def collect() -> None:
        async for _ in refresh_group_metadata_for_document_ingest(
            "user", MetadataIngestRequest("group"), groups, source, store
        ):
            pass

    with pytest.raises(RuntimeError, match="publication failed"):
        asyncio.run(collect())

    assert store.published_drive_ids == ["first"]
    assert [value.id.id[0] for value in store.published] == ["first"]
    assert store.staged == {}
    assert store.operation_status == "failed"
    assert store.drive_outcomes["first"]["status"] == "published"
    assert store.drive_outcomes["second"]["status"] == "failed"


def test_commit_then_raise_keeps_published_outcome_in_failed_parent_summary() -> None:
    groups = ThreeDriveGroups(("first",))
    source = MetadataSource()
    store = CombinedStore(commit_then_raise_drive="first")

    async def collect() -> None:
        async for _ in refresh_group_metadata_for_document_ingest(
            "user", MetadataIngestRequest("group"), groups, source, store
        ):
            pass

    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        asyncio.run(collect())

    assert store.published_drive_ids == ["first"]
    assert [value.id.id[0] for value in store.published] == ["first"]
    assert store.staged == {}
    assert store.operation_status == "failed"
    assert store.drive_outcomes["first"]["status"] == "published"
    outcomes = store.operation_summary["drive_outcomes"]
    assert isinstance(outcomes, dict)
    assert outcomes["first"]["status"] == "published"


def test_cancel_during_publication_discards_prepared_drive() -> None:
    groups = ThreeDriveGroups(("first", "second"))
    source = MetadataSource()
    store = CombinedStore()
    release_publish = asyncio.Event()
    publish_metadata_drive = store.publish_metadata_drive

    async def blocked_publish(
        operation_id: UUID,
        drive_id: str,
        publication: MetadataRunPublication,
        outcome: dict[str, object],
    ) -> None:
        assert operation_id == store.operation_id
        if drive_id != "second":
            await publish_metadata_drive(
                operation_id, drive_id, publication, outcome
            )
            return
        store.publish_started.set()
        await release_publish.wait()

    store.publish_metadata_drive = blocked_publish  # type: ignore[method-assign]

    async def cancel_while_publishing() -> None:
        updates = refresh_group_metadata_for_document_ingest(
            "user", MetadataIngestRequest("group"), groups, source, store
        )
        assert isinstance(await anext(updates), MetadataIngestStarted)
        while True:
            event = await anext(updates)
            if isinstance(event, MetadataIngestProgress) and event.drive_id == "second":
                pending = asyncio.create_task(anext(updates))
                await asyncio.wait_for(store.publish_started.wait(), timeout=1)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                break

    asyncio.run(cancel_while_publishing())

    assert store.published_drive_ids == ["first"]
    assert [value.id.id[0] for value in store.published] == ["first"]
    assert store.staged == {}
    assert store.operation_status == "interrupted"
    assert store.drive_outcomes["first"]["status"] == "published"
    assert store.drive_outcomes["second"]["status"] == "interrupted"
