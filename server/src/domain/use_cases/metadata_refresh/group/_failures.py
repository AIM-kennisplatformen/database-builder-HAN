"""Pure mapping of group metadata refresh errors to durable failure details."""

from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataRunAlreadyActive,
    MetadataSourceError,
    MetadataSourceUnavailable,
    MetadataStoreUnavailable,
)
from domain.use_cases.group_drive_discovery import GroupDriveNotAccessible
from domain.use_cases.metadata_refresh.group._models import MetadataDriveIngestFailure


def _failure_cause(exc: Exception, summary: dict[str, object]) -> Exception:
    match exc:
        case MetadataDriveIngestFailure(drive_id=drive_id, cause=drive_cause):
            summary["failed_drive_id"] = drive_id
            return drive_cause
        case _ as cause:
            return cause


def build_failure_details(
    exc: Exception,
    processed_document_count: int,
    skipped_drive_ids: list[str],
) -> tuple[str, dict[str, object]]:
    summary: dict[str, object] = {
        "processed": processed_document_count,
        "skipped_drive_ids": skipped_drive_ids,
    }
    cause = _failure_cause(exc, summary)

    match cause:
        case MetadataSourceError(status_code=403):
            summary["source_status"] = 403
            summary["reason"] = "Drive is not accessible"
            return "metadata.source_error", summary
        case MetadataSourceError(status_code=status_code):
            summary["source_status"] = status_code
            summary["reason"] = "Metadata source request failed"
            return "metadata.source_error", summary
        case MetadataRunAlreadyActive():
            summary["reason"] = "A metadata run is already active"
            return "metadata.run_already_active", summary
        case GroupDriveNotAccessible():
            summary["reason"] = "Group is not accessible"
            return "metadata.group_not_accessible", summary
        case InvalidMetadataSelection():
            summary["reason"] = "Metadata selection is invalid"
            return "metadata.invalid_selection", summary
        case MetadataSourceUnavailable():
            summary["reason"] = "Metadata source is unavailable"
            return "metadata.source_unavailable", summary
        case MetadataStoreUnavailable():
            summary["reason"] = "Metadata store is unavailable"
            return "metadata.store_unavailable", summary
        case _:
            summary["reason"] = "Metadata ingestion failed"
            return "metadata.ingest_failed", summary
