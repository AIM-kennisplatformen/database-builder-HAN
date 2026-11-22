from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from domain.models.documents.document import (
    Document,
)
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    MetadataSelection,
)
from domain.ports.documents.document_store import (
    MetadataIngestRun,
    MetadataRunPublication,
)
from domain.ports.documents.group_drive_source import (
    GroupDriveLocation,
    UserGroup,
)


def selection(
    extensions: list[str] | None = None,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    exclude_extensions: list[str] | None = None,
) -> MetadataSelection:
    selected_extensions = tuple(extensions or [])
    included = tuple(
        MetadataPathRule(path, selected_extensions)
        for path in (include_paths or ([""] if selected_extensions else []))
    )
    excluded = tuple(MetadataPathRule(path, ()) for path in (exclude_paths or [])) + (
        (MetadataPathRule("", tuple(exclude_extensions)),) if exclude_extensions else ()
    )
    return MetadataSelection(included, excluded)


def selection_arguments(
    selection: MetadataSelection,
) -> tuple[list[str], list[str], list[str], list[str]]:
    include_paths = [rule.path for rule in selection.include_rules if rule.path]
    exclude_paths = [
        rule.path
        for rule in selection.exclude_rules
        if rule.path and not rule.extensions
    ]
    extensions: list[str] = []
    if selection.include_rules:
        extensions = list(selection.include_rules[0].extensions)
        for rule in selection.include_rules:
            if not rule.path:
                extensions = list(rule.extensions)
                break
    exclude_extensions: list[str] = []
    for rule in selection.exclude_rules:
        if not rule.path and rule.extensions:
            exclude_extensions = list(rule.extensions)
            break
    return extensions, include_paths, exclude_paths, exclude_extensions


class OneGroupSource:
    async def stream_user_groups(self, user_id: str) -> AsyncIterator[UserGroup]:
        assert user_id == "user"
        yield UserGroup("group", "Research")

    async def stream_group_drive_ids(self, group_id: str) -> AsyncIterator[str]:
        assert group_id == "group"
        yield "bad-drive"
        yield "drive"

    async def stream_group_drive_locations(
        self, group_id: str
    ) -> AsyncIterator[GroupDriveLocation]:
        async for drive_id in self.stream_group_drive_ids(group_id):
            yield GroupDriveLocation(drive_id)


class MetadataRefreshStore:
    def __init__(self) -> None:
        self.cursor: str | None = None
        self.staged: list[Document] = []
        self.published: list[Document] = []
        self.operation_id: UUID | None = None
        self.operation_status: str | None = None

    async def begin_group_operation(self, principal_id: str, group_id: str) -> UUID:
        _ = (principal_id, group_id)
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
        _ = (principal_id, drive_id, selection, operation_id)
        return MetadataIngestRun(uuid4(), self.cursor, True)

    async def stage_document(self, run_id: UUID, document: Document) -> None:
        _ = run_id
        self.staged.append(document)

    async def stage_deleted_item(self, run_id: UUID, item_id: str) -> None:
        _ = (run_id, item_id)

    async def publish_metadata_run(
        self, run_id: UUID, delta_link: str, baseline: bool, reconcile: bool
    ) -> None:
        _ = (run_id, baseline, reconcile)
        self.published.extend(self.staged)
        self.staged.clear()
        self.cursor = delta_link

    async def publish_metadata_runs(
        self,
        publications: tuple[MetadataRunPublication, ...],
        operation_id: UUID | None = None,
        summary: dict[str, object] | None = None,
        domain_ids: dict[str, object] | None = None,
    ) -> None:
        assert operation_id == self.operation_id
        _ = (summary, domain_ids)
        self.operation_status = "complete"
        self.published.extend(self.staged)
        self.staged.clear()
        if publications:
            self.cursor = publications[-1].delta_link

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
        _ = run_id
        self.staged.clear()
