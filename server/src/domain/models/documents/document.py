from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import override

from domain.models.base import ID, AggregateRoot, ValueObject
from domain.models.documents.content_version import ContentVersion


@dataclass(frozen=True)
class AuditActor(ValueObject):
    display_name: str

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("Audit actor must have a display name.")


@dataclass(frozen=True)
class AuditEntry(ValueObject):
    timestamp: datetime
    actor: AuditActor | None = None


@dataclass(frozen=True)
class DocumentMetadata(ValueObject):
    created: AuditEntry | None = None
    updated: AuditEntry | None = None


@dataclass(frozen=True)
class FileHashes(ValueObject):
    sha1: str | None = None
    sha256: str | None = None
    quick_xor: str | None = None


@dataclass(frozen=True)
class FileMetadata(ValueObject):
    name: str
    mime_type: str | None
    size_bytes: int | None
    content_version: ContentVersion
    source_url: str | None
    hashes: FileHashes = field(default_factory=FileHashes)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("File name cannot be empty.")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("File size cannot be negative.")


@dataclass(frozen=True)
class DocumentId(ID[tuple[str, str]]):
    _container_id: str
    _item_id: str

    @property
    @override
    def id(self) -> tuple[str, str]:
        return self._container_id, self._item_id


@dataclass(eq=False)
class Document(AggregateRoot[DocumentId]):
    file_metadata: FileMetadata
    document_metadata: DocumentMetadata
