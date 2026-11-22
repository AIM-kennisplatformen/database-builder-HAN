"""Shared request, event, and failure models for metadata group ingestion."""

from dataclasses import dataclass, field
from uuid import UUID

from domain.use_cases.metadata_refresh.drive.refresh import MetadataDriveIngestProgress


@dataclass(frozen=True)
class MetadataDriveSelector:
    """Restrict a drive scan to folder paths and file extensions."""

    drive_id: str
    paths: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MetadataGroupIngestRequest:
    """Select group drives and paths for a metadata refresh."""

    group_id: str
    include: list[MetadataDriveSelector] = field(default_factory=list)
    exclude: list[MetadataDriveSelector] = field(default_factory=list)


@dataclass(frozen=True)
class MetadataGroupIngestProgress:
    """Report a drive document and its cumulative group processing count."""

    drive_id: str
    progress: MetadataDriveIngestProgress
    processed: int = 0


@dataclass(frozen=True)
class MetadataGroupIngestSkipped:
    """Report a drive skipped because the source denied access."""

    drive_id: str
    reason: str
    processed: int = 0


@dataclass(frozen=True)
class MetadataGroupIngestResult:
    """Summarize the atomic group refresh and drives skipped as inaccessible."""

    processed: int
    skipped_drive_ids: tuple[str, ...]


@dataclass(frozen=True)
class MetadataGroupIngestStarted:
    """Identify the operation created for the group refresh."""

    operation_id: UUID


@dataclass(frozen=True)
class MetadataDrivePublished:
    """Identify a drive whose metadata and cursor were published."""

    drive_id: str
    metadata_run_id: UUID
    processed: int


@dataclass(frozen=True)
class MetadataDriveFilesSelected:
    """Report the number of document candidates selected for one drive."""

    drive_id: str
    metadata_run_id: UUID
    selected_count: int


@dataclass(frozen=True)
class MetadataDriveRefreshFailure:
    """Describe a drive-level failure or access skip in an independent refresh."""

    drive_id: str
    outcome_status: str
    failure_code: str
    detail: str
    processed: int


@dataclass(frozen=True)
class MetadataRefreshRunResult:
    """Summarize an independent refresh with its failed and skipped drives."""

    processed: int
    failed_drive_ids: tuple[str, ...]
    skipped_drive_ids: tuple[str, ...]


class MetadataDriveIngestFailure(Exception):
    """Wrap a drive refresh error while retaining the affected drive ID."""

    def __init__(self, drive_id: str, cause: Exception) -> None:
        """Create a failure associated with one drive.

        Args:
            drive_id: ID of the drive that failed.
            cause: Original error used as the exception message and retained for
                failure classification.
        """
        super().__init__(str(cause))
        self.drive_id: str = drive_id
        self.cause: Exception = cause
