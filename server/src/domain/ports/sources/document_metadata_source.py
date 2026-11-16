from collections.abc import AsyncIterator
from typing import Protocol

from domain.models.documents.document import Document


class DocumentMetadataSource(Protocol):
    def stream_metadata(
        self, drive_id: str, extensions: list[str], limit: int | None
    ) -> AsyncIterator[tuple[Document, int, int]]: ...
