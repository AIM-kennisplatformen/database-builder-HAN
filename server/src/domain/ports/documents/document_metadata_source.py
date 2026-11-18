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
        self,
        drive_id: str,
        extensions: list[str],
        include_paths: list[str],
        exclude_paths: list[str],
        exclude_extensions: list[str],
        root_item_id: str,
    ) -> AsyncIterator[DocumentMetadataItem]: ...
