import asyncio
from uuid import UUID

import anyio
import pytest
from _metadata_document_ingest_fixtures import (
    CandidateStoreFailure,
    CombinedStore,
    MetadataSource,
    ThreeDriveGroups,
)

from domain.models.documents.document_ingestion import (
    DocumentIngestRequest,
    DocumentProcessingCandidate,
)
from domain.ports.documents.document_store import (
    MetadataRunPublication,
)
from domain.use_cases.document_ingestion.select_candidates import (
    refresh_and_select_group_document_candidates,
)
from domain.use_cases.metadata_refresh import (
    MetadataDriveFilesSelected,
    MetadataDrivePublished,
    MetadataIngestProgress,
    MetadataIngestRequest,
    MetadataIngestStarted,
)
from domain.use_cases.metadata_refresh.group.per_drive import (
    refresh_group_metadata_per_drive,
)


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
        async for event in refresh_and_select_group_document_candidates(
            "user", request, groups, MetadataSource(), store
        ):
            seen_events.append(event)

    with pytest.raises(expected_error):
        asyncio.run(collect())

    assert store.operation_status == "failed"
    assert store.operation_failure_code == "document_ingest.selection_failed"
    assert store.operation_summary["failure_stage"] == "document_ingest.selection"
    assert store.drive_outcomes["first"]["status"] == "published"
    assert not any(
        isinstance(event, MetadataDriveFilesSelected) for event in seen_events
    )


def test_cancellation_keeps_published_drive_and_marks_active_drive_interrupted() -> (
    None
):
    groups = ThreeDriveGroups(("first", "second"))
    source = MetadataSource()
    store = CombinedStore()

    async def cancel_during_second_drive() -> None:
        updates = refresh_group_metadata_per_drive(
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
        updates = refresh_group_metadata_per_drive(
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
        updates = refresh_group_metadata_per_drive(
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


def test_publish_failure_discards_prepared_drive_and_preserves_prior_publication() -> (
    None
):
    groups = ThreeDriveGroups(("first", "second"))
    source = MetadataSource()
    store = CombinedStore(fail_publish_drive="second")

    async def collect() -> None:
        async for _ in refresh_group_metadata_per_drive(
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
        async for _ in refresh_group_metadata_per_drive(
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
            await publish_metadata_drive(operation_id, drive_id, publication, outcome)
            return
        store.publish_started.set()
        await release_publish.wait()

    store.publish_metadata_drive = blocked_publish  # type: ignore[method-assign]

    async def cancel_while_publishing() -> None:
        updates = refresh_group_metadata_per_drive(
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
