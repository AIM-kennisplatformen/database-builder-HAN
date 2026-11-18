from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from domain.models.documents.document import Document


@dataclass(frozen=True)
class DocumentMetadataRun:
    id: UUID
    cursor: str | None
    same_selection: bool


class DocumentStore(Protocol):
    async def save_document(self, document: Document) -> None: ...


class DocumentRefreshStore(Protocol):
    async def begin_metadata_run(
        self, principal_id: str, drive_id: str, selection: str
    ) -> DocumentMetadataRun: ...

    async def stage_document(self, run_id: UUID, document: Document) -> None: ...

    async def stage_deleted(self, run_id: UUID, item_id: str) -> None: ...

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None: ...

    async def fail_metadata_run(self, run_id: UUID) -> None: ...
