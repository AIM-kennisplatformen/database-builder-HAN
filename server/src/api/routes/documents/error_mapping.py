"""Public error mappings shared by document metadata endpoints."""

from api.errors import PublicError, build_internal_error
from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataRunAlreadyActive,
    MetadataSourceError,
    MetadataSourceUnavailable,
    MetadataStoreUnavailable,
)
from domain.use_cases.group_drive_discovery import GroupDriveNotAccessible
from domain.use_cases.metadata_refresh import MetadataDriveIngestFailure


class MetadataErrorMapper:
    @staticmethod
    def describe(exc: Exception) -> PublicError:
        if isinstance(exc, MetadataDriveIngestFailure):
            exc = exc.cause
        if isinstance(exc, MetadataSourceError):
            if exc.status_code in (403, 404):
                status = exc.status_code
            elif exc.status_code == 429:
                status = 503
            else:
                status = 502
            return PublicError(
                "metadata.source_error", status, "Metadata source request failed"
            )
        if isinstance(exc, MetadataRunAlreadyActive):
            return PublicError(
                "metadata.run_already_active", 409, "A metadata run is already active"
            )
        if isinstance(exc, GroupDriveNotAccessible):
            return PublicError(
                "metadata.group_not_accessible", 404, "Group is not accessible"
            )
        if isinstance(exc, InvalidMetadataSelection):
            return PublicError("metadata.invalid_selection", 422, str(exc))
        if isinstance(exc, MetadataSourceUnavailable):
            return PublicError(
                "metadata.source_unavailable", 503, "Metadata source is unavailable"
            )
        if isinstance(exc, MetadataStoreUnavailable):
            return PublicError(
                "metadata.store_unavailable", 503, "Metadata store is unavailable"
            )
        return build_internal_error()
