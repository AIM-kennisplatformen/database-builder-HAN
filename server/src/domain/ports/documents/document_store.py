from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from domain.models.documents.document import Document
from domain.models.documents.document_ingestion import (
    DocumentFileIngestSelection,
    DocumentProcessingCandidate,
    ProcessingOutcome,
)
from domain.models.documents.metadata_selection import MetadataSelection


@dataclass(frozen=True)
class MetadataDriveIngestRun:
    id: UUID
    cursor: str | None
    same_selection: bool


@dataclass(frozen=True)
class MetadataDriveRunPublication:
    run_id: UUID
    delta_link: str
    baseline: bool
    reconcile: bool


class DocumentStore(Protocol):
    async def save_document(self, document: Document) -> None: ...


class DocumentRefreshStore(Protocol):
    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID: ...

    async def begin_metadata_run(
        self,
        principal_id: str,
        drive_id: str,
        selection: MetadataSelection,
        operation_id: UUID | None = None,
    ) -> MetadataDriveIngestRun: ...

    async def stage_document(self, run_id: UUID, document: Document) -> None: ...

    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None: ...

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None: ...

    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataDriveRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None: ...

    async def fail_group_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        *,
        interrupted: bool = False,
        summary: dict[str, object] | None = None,
    ) -> None: ...

    async def discard_metadata_run(self, run_id: UUID) -> None: ...


class CombinedDocumentRefreshStore(DocumentRefreshStore, Protocol):
    """Storage operations used by per-drive combined metadata publication."""

    async def publish_metadata_drive(
        self,
        operation_id: UUID,
        drive_id: str,
        publication: MetadataDriveRunPublication,
        outcome: dict[str, object],
    ) -> None: ...

    async def record_metadata_drive_outcome(
        self, operation_id: UUID, drive_id: str, outcome: dict[str, object]
    ) -> None: ...

    async def complete_group_operation(
        self, operation_id: UUID, summary: dict[str, object]
    ) -> None: ...

    async def query_visible_document_candidates(
        self, principal_id: str, drive_id: str, published_run_id: UUID
    ) -> tuple[DocumentProcessingCandidate, ...]: ...

    async def record_document_ingest_selections(
        self,
        operation_id: UUID,
        selections: tuple[DocumentFileIngestSelection, ...],
    ) -> None: ...

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
    ) -> None: ...


class DocumentMetadataDeleter(Protocol):
    async def delete_all_metadata(self) -> bool: ...
