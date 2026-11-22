"""Pure selector projection for document candidate metadata refresh."""

from domain.models.documents.document_ingestion import DocumentIngestRequest
from domain.use_cases.metadata_refresh.group._models import (
    MetadataDriveSelector,
    MetadataGroupIngestRequest,
)


def build_metadata_refresh_request(
    request: DocumentIngestRequest,
) -> MetadataGroupIngestRequest:
    return MetadataGroupIngestRequest(
        request.group_id,
        include=[
            MetadataDriveSelector(
                selector.drive_id,
                paths=[path for path in selector.paths if path],
                extensions=list(selector.extensions),
            )
            for selector in request.include
        ],
        exclude=[
            MetadataDriveSelector(
                selector.drive_id,
                paths=[path for path in selector.paths if path],
                extensions=list(selector.extensions),
            )
            for selector in request.exclude
            if selector.statuses is None
        ],
    )


def select_path_capture_drive_ids(request: DocumentIngestRequest) -> frozenset[str]:
    return frozenset(
        selector.drive_id
        for selector in request.include + request.exclude
        if selector.statuses is not None and any(selector.paths)
    )
