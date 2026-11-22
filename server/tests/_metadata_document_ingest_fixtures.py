import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast, final, override
from uuid import UUID, uuid4

import anyio

from domain.exceptions.metadata_ingest_errors import (
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
    DocumentFileIngestSelection,
    DocumentProcessingCandidate,
    ProcessingOutcome,
)
from domain.models.documents.metadata_selection import MetadataSelection
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from domain.ports.documents.document_store import (
    MetadataDriveIngestRun,
    MetadataDriveRunPublication,
)
from domain.ports.documents.group_drive_source import GroupDriveLocation, UserGroup
from domain.ports.operation_run_store import OperationRun


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
        self.file_selections: list[
            tuple[UUID, tuple[DocumentFileIngestSelection, ...]]
        ] = []

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
    ) -> MetadataDriveIngestRun:
        _ = (principal_id, selection)
        assert operation_id == self.operation_id
        run_id = uuid4()
        self.run_drives[run_id] = drive_id
        self.operation_domain_ids.setdefault("drive_run_ids", [])
        cast(list[str], self.operation_domain_ids["drive_run_ids"]).append(str(run_id))
        return MetadataDriveIngestRun(run_id, None, True)

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
        publications: tuple[MetadataDriveRunPublication, ...],
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
        publication: MetadataDriveRunPublication,
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
        selections: tuple[DocumentFileIngestSelection, ...],
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
        is_deferred: bool = False,
        failure_stage: str | None = None,
        failure_detail: str | None = None,
    ) -> None:
        _ = (
            drive_id,
            item_id,
            source_content_version,
            processing_policy_version,
            outcome,
            is_deferred,
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
        selections: tuple[DocumentFileIngestSelection, ...],
    ) -> None:
        if self.failure_stage == "record":
            raise RuntimeError("selection persistence failed")
        await super().record_document_ingest_selections(operation_id, selections)
