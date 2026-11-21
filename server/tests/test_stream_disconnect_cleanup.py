import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import anyio

from api.routes.documents.metadata import (
    MetadataIngestRequestBody,
    ingest_group_metadata_endpoint,
)
from api.routes.employees.sync import EmployeesSyncRequestBody, sync_employees_endpoint
from domain.models.documents.content_version import ContentVersion
from domain.models.documents.document import (
    Document,
    DocumentId,
    DocumentMetadata,
    FileMetadata,
)
from domain.models.documents.metadata_selection import MetadataSelection
from domain.models.employee import Employee
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from domain.ports.documents.document_store import (
    MetadataIngestRun,
    MetadataRunPublication,
)
from domain.ports.documents.group_drive_source import GroupDriveLocation, UserGroup
from domain.ports.employees.employee_source import EmployeeStreamItem
from domain.ports.employees.employee_store import (
    EmployeeSyncCounts,
    WriteEmployeeOutcome,
)


class BlockingEmployeeSource:
    async def stream_employees(
        self, limit: int | None
    ) -> AsyncIterator[EmployeeStreamItem]:
        assert limit is None
        yield EmployeeStreamItem(Employee("employee"), total=2, available=2)
        await anyio.Event().wait()


class EmployeeStore:
    def __init__(self) -> None:
        self.staged: list[Employee] = []
        self.committed: list[Employee] = []
        self.discarded: bool = False
        self.status: str | None = None
        self.run_id: UUID | None = None

    async def begin_sync(self, principal_id: str) -> UUID:
        assert principal_id == "user"
        self.run_id = uuid4()
        self.status = "running"
        return self.run_id

    async def stage_employee(
        self, run_id: UUID, employee: Employee
    ) -> WriteEmployeeOutcome:
        _ = run_id
        self.staged.append(employee)
        return WriteEmployeeOutcome.CREATED

    async def publish_sync(self, run_id: UUID) -> EmployeeSyncCounts:
        _ = run_id
        self.committed.extend(self.staged)
        self.staged.clear()
        return EmployeeSyncCounts(len(self.committed), 0, 0)

    async def discard_sync(
        self,
        run_id: UUID,
        *,
        interrupted: bool = False,
        failure_code: str = "employee.sync_failed",
        summary: dict[str, object] | None = None,
    ) -> None:
        _ = run_id
        _ = (failure_code, summary)
        await anyio.lowlevel.checkpoint()
        self.staged.clear()
        self.discarded = True
        self.status = "interrupted" if interrupted else "failed"


def test_employee_endpoint_close_marks_operation_interrupted() -> None:
    store = EmployeeStore()

    async def close_after_started() -> None:
        endpoint = sync_employees_endpoint(
            EmployeesSyncRequestBody(), "user", BlockingEmployeeSource(), store
        )
        event = await anext(endpoint)
        assert event.event == "started"
        await endpoint.aclose()

    asyncio.run(close_after_started())
    assert store.discarded
    assert store.staged == []
    assert store.committed == []
    assert store.status == "interrupted"


class GroupSource:
    async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        assert user_id == "user"
        yield UserGroup("group", "Research")

    async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        assert group_id == "group"
        yield "drive"

    async def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]:
        async for drive_id in self.stream_group_drive_ids(group_id):
            yield GroupDriveLocation(drive_id, "root")


class BlockingMetadataSource:
    async def scan_metadata(
        self,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str,
        cursor: str | None,
    ) -> DocumentMetadataScan:
        _ = selection
        assert (drive_id, root_item_id, cursor) == ("drive", "root", None)
        documents = tuple(
            Document(
                _id=DocumentId("drive", f"item-{index}"),
                file_metadata=FileMetadata(
                    name=f"Report-{index}.pdf",
                    mime_type="application/pdf",
                    size_bytes=10,
                    content_version=ContentVersion("v1"),
                    source_url=None,
                ),
                document_metadata=DocumentMetadata(),
            )
            for index in range(2)
        )
        return DocumentMetadataScan(documents, (), "next", False)


class BlockingMetadataStore:
    def __init__(self) -> None:
        self.run_id: UUID | None = None
        self.operation_id: UUID | None = None
        self.staged: list[Document] = []
        self.published: list[Document] = []
        self.cleaned: bool = False
        self.stage_count: int = 0
        self.operation_status: str | None = None

    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
        assert (principal_id, group_id) == ("user", "group")
        self.operation_id = uuid4()
        self.operation_status = "running"
        return self.operation_id

    async def begin_metadata_run(
        self,
        principal_id: str,
        drive_id: str,
        selection: MetadataSelection,
        operation_id: UUID | None = None,
    ) -> MetadataIngestRun:
        assert (principal_id, drive_id) == ("user", "drive")
        _ = (selection, operation_id)
        run_id = uuid4()
        self.run_id = run_id
        return MetadataIngestRun(run_id, None, True)

    async def stage_document(self, run_id: UUID, document: Document) -> None:
        assert run_id == self.run_id
        self.stage_count += 1
        if self.stage_count == 2:
            await anyio.Event().wait()
        self.staged.append(document)

    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
        _ = (run_id, item_id)

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        _ = (run_id, delta_link, baseline, reconcile)
        self.published.extend(self.staged)
        self.staged.clear()

    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None:
        assert operation_id == self.operation_id
        _ = (publications, summary, domain_ids)
        self.published.extend(self.staged)
        self.staged.clear()
        self.operation_status = "complete"

    async def fail_group_operation(
        self,
        operation_id: UUID,
        failure_code: str,
        *,
        interrupted: bool = False,
        summary: dict[str, object] | None = None,
    ) -> None:
        assert operation_id == self.operation_id
        _ = (failure_code, summary)
        self.operation_status = "interrupted" if interrupted else "failed"

    async def discard_metadata_run(self, run_id: UUID) -> None:
        assert run_id == self.run_id
        await anyio.lowlevel.checkpoint()
        self.staged.clear()
        self.cleaned = True


def test_metadata_endpoint_close_marks_operation_interrupted() -> None:
    store = BlockingMetadataStore()

    async def close_after_started() -> None:
        endpoint = ingest_group_metadata_endpoint(
            "group",
            MetadataIngestRequestBody(),
            "user",
            GroupSource(),
            BlockingMetadataSource(),
            store,
        )
        event = await anext(endpoint)
        assert event.event == "started"
        await endpoint.aclose()

    asyncio.run(close_after_started())
    assert store.operation_status == "interrupted"
    assert store.staged == []
    assert store.published == []
