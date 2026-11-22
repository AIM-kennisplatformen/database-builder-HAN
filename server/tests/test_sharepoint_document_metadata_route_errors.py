import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import override

import pytest
from pydantic import ValidationError
from sharepoint_document_metadata_test_support import (
    MetadataRefreshStore,
    OneGroupSource,
    selection_arguments,
)

from api.routes.documents import metadata as metadata_route
from api.routes.documents.metadata import (
    DriveMetadataSelectorBody,
    MetadataIngestRequestBody,
    ingest_group_metadata_endpoint,
)
from domain.exceptions.metadata_ingest_errors import (
    MetadataSourceError,
)
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.metadata_selection import (
    MetadataSelection,
)
from domain.ports.documents.document_metadata_source import (
    DocumentMetadataScan,
)


@pytest.mark.parametrize(
    "removed_field",
    [{"limit": 10}, {"path": "Reports"}, {"include_drive_ids": ["drive"]}],
)
def test_metadata_request_rejects_removed_fields(
    removed_field: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        MetadataIngestRequestBody.model_validate(removed_field)


def test_metadata_selector_requires_paths_list() -> None:
    with pytest.raises(ValidationError):
        MetadataIngestRequestBody.model_validate(
            {"include": [{"drive_id": "drive", "paths": "Reports/"}]}
        )


def test_metadata_selector_rejects_ingestion_statuses() -> None:
    with pytest.raises(ValidationError):
        MetadataIngestRequestBody.model_validate(
            {
                "include": [
                    {
                        "drive_id": "drive",
                        "paths": ["Reports"],
                        "statuses": ["new"],
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    ("graph_status", "event_status"),
    [(403, 403), (404, 404), (429, 503), (400, 502), (500, 502)],
)
def test_metadata_endpoint_returns_graph_client_error(
    graph_status: int,
    event_status: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setattr(
        metadata_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    class ErrorSource:
        def __init__(self) -> None:
            self.status_code: int = graph_status

        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            extensions, include_paths, exclude_paths, exclude_extensions = (
                selection_arguments(selection)
            )
            assert cursor is None
            assert root_item_id == "root"
            assert (
                drive_id,
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
            ) == (
                "bad-drive",
                ["pdf"],
                [],
                [],
                [],
            )
            raise MetadataSourceError(
                self.status_code, "The supplied drive id is invalid"
            )

    request = MetadataIngestRequestBody(
        include=[DriveMetadataSelectorBody(drive_id="bad-drive", extensions=["pdf"])],
    )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                request,
                "user",
                OneGroupSource(),
                ErrorSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "metadata.source_error",
            "http_status": event_status,
            "detail": "Metadata source request failed",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": "bad-drive",
            "operation_id": operation_id,
        },
        "progress": {"processed": 0},
    }
    expected_logs = 1 if event_status >= 500 else 0
    api_failure_records = [
        record for record in caplog.records if record.name == "api.errors"
    ]
    assert len(api_failure_records) == expected_logs


def test_metadata_endpoint_failure_reports_group_progress_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        metadata_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    class DriveThenFailureGroup(OneGroupSource):
        @override
        async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
            assert group_id == "group"
            yield "drive"
            yield "bad-drive"

    class FailureAfterProgressSource:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            extensions, include_paths, exclude_paths, exclude_extensions = (
                selection_arguments(selection)
            )
            _ = (
                extensions,
                include_paths,
                exclude_paths,
                exclude_extensions,
                root_item_id,
                cursor,
            )
            if drive_id == "bad-drive":
                raise MetadataSourceError(503, "Graph unavailable")
            return DocumentMetadataScan(
                (
                    Document(
                        _id=DocumentId("drive", "item"),
                        file_metadata=FileMetadata(
                            name="Report.pdf",
                            mime_type="application/pdf",
                            size_bytes=10,
                            content_version=ContentVersion("v1"),
                            source_url=None,
                        ),
                        document_metadata=DocumentMetadata(),
                    ),
                ),
                (),
                "new-cursor",
                False,
            )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                DriveThenFailureGroup(),
                FailureAfterProgressSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "progress", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data["processed"] == 1
    assert events[2].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "metadata.source_error",
            "http_status": 502,
            "detail": "Metadata source request failed",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": "bad-drive",
            "operation_id": operation_id,
        },
        "progress": {"processed": 1},
    }
