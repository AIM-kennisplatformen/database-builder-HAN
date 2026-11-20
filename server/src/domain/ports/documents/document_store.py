from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from domain.models.documents.document import Document
from domain.models.documents.metadata_selection import MetadataSelection


@dataclass(frozen=True)
class MetadataIngestRun:
    id: UUID
    cursor: str | None
    same_selection: bool


@dataclass(frozen=True)
class MetadataRunPublication:
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
    ) -> MetadataIngestRun: ...

    async def stage_document(self, run_id: UUID, document: Document) -> None: ...

    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None: ...

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None: ...

    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataRunPublication, ...],
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


class DocumentMetadataDeleter(Protocol):
    async def delete_all_metadata(self) -> bool: ...
