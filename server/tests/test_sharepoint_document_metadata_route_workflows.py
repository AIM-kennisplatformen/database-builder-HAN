import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast, override
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
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
    MetadataRunAlreadyActive,
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
    DocumentRefreshSource,
)
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    MetadataIngestRun,
)
from domain.ports.documents.group_drive_source import (
    GroupDriveSource,
    UserGroup,
)


def test_metadata_only_endpoint_keeps_group_atomic_publication() -> None:
    class TwoDriveGroup(OneGroupSource):
        @override
        async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
            assert group_id == "group"
            yield "first-drive"
            yield "second-drive"

    class FailSecondDrive:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            _ = (selection, root_item_id)
            assert cursor is None
            if drive_id == "second-drive":
                raise MetadataSourceError(503, "Graph unavailable")
            return DocumentMetadataScan(
                (
                    Document(
                        _id=DocumentId("first-drive", "item"),
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
                "first-cursor",
                True,
            )

    store = MetadataRefreshStore()

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                TwoDriveGroup(),
                FailSecondDrive(),
                store,
            )
        ]

    events = asyncio.run(collect())

    assert [event.event for event in events] == ["started", "progress", "error"]
    assert store.published == []
    assert store.staged == []
    assert store.operation_status == "failed"


def test_metadata_known_error_subclass_inherits_status_and_hides_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEBUG", "true")

    class SpecializedRunAlreadyActive(MetadataRunAlreadyActive):
        pass

    class AlreadyActiveStore(MetadataRefreshStore):
        @override
        async def begin_metadata_run(
            self,
            principal_id: str,
            drive_id: str,
            selection: MetadataSelection,
            operation_id: UUID | None = None,
        ) -> MetadataIngestRun:
            _ = (principal_id, drive_id, selection, operation_id)
            raise SpecializedRunAlreadyActive("metadata run is already active")

    monkeypatch.setattr(
        metadata_route,
        "config",
        SimpleNamespace(stream_progress_interval_seconds=1),
    )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                OneGroupSource(),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                AlreadyActiveStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "metadata.run_already_active",
            "http_status": 409,
            "detail": "A metadata run is already active",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": "bad-drive",
            "operation_id": operation_id,
        },
        "progress": {"processed": 0},
    }


def test_metadata_endpoint_skips_inaccessible_drive_and_completes() -> None:
    class PartlyAccessibleSource:
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
            assert (extensions, include_paths, exclude_paths, exclude_extensions) == (
                [],
                [],
                [],
                [],
            )
            if drive_id == "bad-drive":
                raise MetadataSourceError(403, "Access denied")
            return DocumentMetadataScan(
                (
                    Document(
                        _id=DocumentId(drive_id, "item"),
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
                OneGroupSource(),
                PartlyAccessibleSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == [
        "started",
        "skipped",
        "progress",
        "complete",
    ]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "skipped",
        "operation_id": operation_id,
        "group_id": "group",
        "drive_id": "bad-drive",
        "processed": 0,
        "error_status": 403,
        "detail": "Drive is not accessible",
    }
    assert events[2].data == {
        "status": "progress",
        "operation_id": operation_id,
        "group_id": "group",
        "drive_id": "drive",
        "processed": 1,
        "total": 1,
        "available": 1,
        "item_id": "item",
        "name": "Report.pdf",
    }
    assert events[3].data == {
        "status": "complete",
        "operation_id": operation_id,
        "group_id": "group",
        "processed": 1,
        "skipped_drive_ids": ["bad-drive"],
    }


def test_metadata_endpoint_reports_explicitly_included_drive_denial() -> None:
    class DeniedSource:
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
            assert drive_id == "bad-drive"
            assert (extensions, include_paths, exclude_paths, exclude_extensions) == (
                [],
                [],
                [],
                [],
            )
            assert root_item_id == "root"
            raise MetadataSourceError(403, "Access denied")

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[DriveMetadataSelectorBody(drive_id="bad-drive")]
                ),
                "user",
                OneGroupSource(),
                DeniedSource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    assert events[1].data["context"]["drive_id"] == "bad-drive"
    assert events[1].data["error"] == {
        "code": "metadata.source_error",
        "http_status": 403,
        "detail": "Metadata source request failed",
    }


def test_metadata_endpoint_reports_explicit_drive_filtered_by_discovery() -> None:
    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[DriveMetadataSelectorBody(drive_id="hidden-drive")]
                ),
                "user",
                OneGroupSource(),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                cast(DocumentRefreshStore, cast(object, AsyncMock())),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    assert events[1].data["context"]["drive_id"] == "hidden-drive"
    assert events[1].data["error"] == {
        "code": "metadata.source_error",
        "http_status": 403,
        "detail": "Metadata source request failed",
    }
    assert events[1].data["progress"]["processed"] == 0


def test_metadata_repeated_same_drive_selectors_are_accepted() -> None:
    class EmptySource:
        async def scan_metadata(
            self,
            drive_id: str,
            selection: MetadataSelection,
            root_item_id: str,
            cursor: str | None,
            capture_relative_paths: bool = False,
        ) -> DocumentMetadataScan:
            _ = capture_relative_paths
            assert drive_id == "drive"
            assert selection.includes("reports/one", "pdf")
            assert selection.includes("reports/two", "docx")
            assert root_item_id == "root"
            assert cursor is None
            return DocumentMetadataScan((), (), "new-cursor", True)

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(
                    include=[
                        DriveMetadataSelectorBody(
                            drive_id="drive", paths=["Reports"], extensions=["pdf"]
                        ),
                        DriveMetadataSelectorBody(
                            drive_id="drive", paths=["Reports"], extensions=["docx"]
                        ),
                    ]
                ),
                "user",
                OneGroupSource(),
                EmptySource(),
                MetadataRefreshStore(),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "complete"]
    assert events[1].data["processed"] == 0


def test_metadata_endpoint_reports_and_logs_unexpected_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenGroupSource:
        async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
            if user_id:
                raise RuntimeError("discovery failed")
            yield UserGroup("unreachable", None)

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                cast(GroupDriveSource, cast(object, BrokenGroupSource())),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                cast(DocumentRefreshStore, cast(object, AsyncMock())),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["started", "error"]
    operation_id = events[0].data["operation_id"]
    assert events[1].data == {
        "status": "error",
        "operation_id": operation_id,
        "error": {
            "code": "internal_error",
            "http_status": 500,
            "detail": "The operation failed",
        },
        "context": {
            "operation": "metadata.ingest",
            "group_id": "group",
            "drive_id": None,
            "operation_id": operation_id,
        },
        "progress": {"processed": 0},
    }
    assert "discovery failed" not in str(events[1].data)
    failure_records = [record for record in caplog.records if record.exc_info]
    assert len(failure_records) == 1
    assert failure_records[0].exc_info is not None
    assert isinstance(failure_records[0].exc_info[1], RuntimeError)
    assert sum(record.name == "api.errors" for record in caplog.records) == 1
    assert "discovery failed" in caplog.text


def test_metadata_endpoint_logs_missing_outcome(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def empty_ingest(*args: object) -> AsyncIterator[object]:
        if len(args) == 0:
            yield object()

    monkeypatch.setattr(
        metadata_route, "refresh_group_metadata_atomically", empty_ingest
    )

    async def collect():
        return [
            event
            async for event in ingest_group_metadata_endpoint(
                "group",
                MetadataIngestRequestBody(),
                "user",
                OneGroupSource(),
                cast(DocumentRefreshSource, cast(object, AsyncMock())),
                cast(DocumentRefreshStore, cast(object, AsyncMock())),
            )
        ]

    events = asyncio.run(collect())
    assert [event.event for event in events] == ["error"]
    assert events[0].data["error"] == {
        "code": "internal_error",
        "http_status": 500,
        "detail": "The operation failed",
    }
    failure_records = [record for record in caplog.records if record.exc_info]
    assert not failure_records
    assert sum(record.name == "api.errors" for record in caplog.records) == 1
    assert "ended without an outcome" in caplog.text
