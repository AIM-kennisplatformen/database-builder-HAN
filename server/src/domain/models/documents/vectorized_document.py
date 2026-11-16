from dataclasses import dataclass
from math import isfinite
from typing import override

from domain.models.base import ID, AggregateRoot, ValueObject
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import DocumentId


@dataclass(frozen=True)
class VectorChunk(ValueObject):
    chunk_index: int
    text: str
    embedding: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.chunk_index < 0:
            raise ValueError("Chunk index cannot be negative.")
        if not self.text.strip():
            raise ValueError("Vector chunk text cannot be empty.")
        if not self.embedding or not all(isfinite(value) for value in self.embedding):
            raise ValueError("Embedding must contain finite values.")


@dataclass(frozen=True)
class VectorizedDocumentId(ID[tuple[DocumentId, ContentVersion]]):
    document_id: DocumentId
    content_version: ContentVersion

    @property
    @override
    def id(self) -> tuple[DocumentId, ContentVersion]:
        return self.document_id, self.content_version


@dataclass(eq=False)
class VectorizedDocument(AggregateRoot[VectorizedDocumentId]):
    chunks: tuple[VectorChunk, ...] = ()

    def __post_init__(self) -> None:
        indices = [chunk.chunk_index for chunk in self.chunks]
        if indices != list(range(len(indices))):
            raise ValueError("Chunks must be ordered and indexed from zero.")
        dimensions = {len(chunk.embedding) for chunk in self.chunks}
        if len(dimensions) > 1:
            raise ValueError(
                "All chunks in a document must have the same embedding size."
            )
