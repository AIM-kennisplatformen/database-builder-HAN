from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from domain.models.documents.document import Document


@dataclass(frozen=True)
class DocumentMetadataItem:
    document: Document
    total: int
    available: int


class DocumentMetadataSource(Protocol):
    def stream_metadata(
        self, drive_id: str, extensions: list[str], limit: int | None
    ) -> AsyncIterator[DocumentMetadataItem]: ...
