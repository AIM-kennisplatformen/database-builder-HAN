"""Pure selector projection for document candidate metadata refresh."""

from domain.models.documents.document_ingestion import DocumentIngestRequest
from domain.use_cases.metadata_refresh.group._models import (
    DriveMetadataSelector,
    MetadataIngestRequest,
)


def metadata_refresh_request(
    request: DocumentIngestRequest,
) -> MetadataIngestRequest:
    return MetadataIngestRequest(
        request.group_id,
        include=[
            DriveMetadataSelector(
                selector.drive_id,
                paths=[path for path in selector.paths if path],
                extensions=list(selector.extensions),
            )
            for selector in request.include
        ],
        exclude=[
            DriveMetadataSelector(
                selector.drive_id,
                paths=[path for path in selector.paths if path],
                extensions=list(selector.extensions),
            )
            for selector in request.exclude
            if selector.statuses is None
        ],
    )


def path_capture_drive_ids(request: DocumentIngestRequest) -> frozenset[str]:
    return frozenset(
        selector.drive_id
        for selector in request.include + request.exclude
        if selector.statuses is not None and any(selector.paths)
    )
