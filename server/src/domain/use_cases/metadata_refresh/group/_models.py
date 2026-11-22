"""Shared request, event, and failure models for metadata group ingestion."""

from dataclasses import dataclass, field
from uuid import UUID

from domain.use_cases.metadata_refresh.drive.refresh import MetadataIngestDriveProgress


@dataclass(frozen=True)
class DriveMetadataSelector:
    drive_id: str
    paths: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MetadataIngestRequest:
    group_id: str
    include: list[DriveMetadataSelector] = field(default_factory=list)
    exclude: list[DriveMetadataSelector] = field(default_factory=list)


@dataclass(frozen=True)
class MetadataIngestProgress:
    drive_id: str
    progress: MetadataIngestDriveProgress
    processed: int = 0


@dataclass(frozen=True)
class MetadataIngestSkipped:
    drive_id: str
    reason: str
    processed: int = 0


@dataclass(frozen=True)
class MetadataIngestResult:
    processed: int
    skipped_drive_ids: tuple[str, ...]


@dataclass(frozen=True)
class MetadataIngestStarted:
    operation_id: UUID


@dataclass(frozen=True)
class MetadataDrivePublished:
    drive_id: str
    metadata_run_id: UUID
    processed: int


@dataclass(frozen=True)
class MetadataDriveFilesSelected:
    drive_id: str
    metadata_run_id: UUID
    selected_count: int


@dataclass(frozen=True)
class MetadataDriveRefreshFailure:
    drive_id: str
    outcome_status: str
    failure_code: str
    detail: str
    processed: int


@dataclass(frozen=True)
class MetadataRefreshRunResult:
    processed: int
    failed_drive_ids: tuple[str, ...]
    skipped_drive_ids: tuple[str, ...]


class MetadataIngestDriveFailure(Exception):
    def __init__(self, drive_id: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.drive_id: str = drive_id
        self.cause: Exception = cause
