from typing import Protocol

from domain.models.documents.document import Document


class DocumentStore(Protocol):
    async def save_document(self, document: Document) -> None: ...
