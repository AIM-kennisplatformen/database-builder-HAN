import asyncio

import pytest
from _metadata_document_ingest_fixtures import (
    CombinedStore,
    MetadataSource,
    ThreeDriveGroups,
)

from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
)
from domain.models.documents.document_ingestion import (
    DocumentIngestRequest,
    DocumentProcessingCandidate,
    DocumentProcessingState,
    IngestionDriveSelector,
    ProcessingOutcome,
    ProcessingStatus,
)
from domain.use_cases.document_ingestion.select_candidates import (
    refresh_and_select_group_document_candidates,
)
from domain.use_cases.metadata_refresh import (
    MetadataDriveFilesSelected,
    MetadataDrivePublished,
    MetadataDriveRefreshFailure,
    MetadataIngestRequest,
    MetadataIngestStarted,
    MetadataRefreshRunResult,
)
from domain.use_cases.metadata_refresh.group.per_drive import (
    refresh_group_metadata_per_drive,
)


def test_combined_refresh_publishes_success_and_records_partial_drive_outcomes() -> (
    None
):
    groups = ThreeDriveGroups(("first", "failed", "denied"))
    source = MetadataSource({"denied": 403})
    store = CombinedStore(fail_stage_drive="failed")

    async def collect() -> list[object]:
        return [
            event
            async for event in refresh_group_metadata_per_drive(
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
            async for event in refresh_and_select_group_document_candidates(
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
            async for event in refresh_and_select_group_document_candidates(
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
        async for _event in refresh_and_select_group_document_candidates(
            "user", request, groups, source, store
        ):
            pass

    with pytest.raises(InvalidMetadataSelection):
        asyncio.run(collect())

    assert source.calls == []
    assert store.published == []
    assert store.staged == {}


def test_path_scoped_status_exclusion_captures_paths_without_narrowing_refresh() -> (
    None
):
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
            async for event in refresh_and_select_group_document_candidates(
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
            async for event in refresh_and_select_group_document_candidates(
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
