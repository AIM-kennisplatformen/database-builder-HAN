from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import override

from domain.models.base import ID, AggregateRoot, ValueObject
from domain.models.documents.content_version import ContentVersion
from domain.models.person import Person, PersonId


@dataclass(frozen=True)
class Author(ValueObject):
    display_name: str
    mail: str | None = None
    person_id: PersonId | None = None

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise ValueError("Author must have a display name.")

    @classmethod
    def from_person(cls, person: Person) -> Author:
        return cls(
            display_name=person.display_name, mail=person.mail, person_id=person.id
        )


@dataclass(frozen=True)
class AuditEntry(ValueObject):
    author: Author
    timestamp: datetime
    content_version: ContentVersion


@dataclass(frozen=True)
class DocumentMetadata(ValueObject):
    authors: tuple[Author, ...] = ()
    update_history: tuple[AuditEntry, ...] = ()

    def __post_init__(self) -> None:
        timestamps = [entry.timestamp for entry in self.update_history]
        if timestamps != sorted(timestamps):
            raise ValueError("Document update history must be in chronological order.")

    @property
    def created(self) -> AuditEntry | None:
        return self.update_history[0] if self.update_history else None

    @property
    def updated(self) -> AuditEntry | None:
        return self.update_history[-1] if len(self.update_history) > 1 else None


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
