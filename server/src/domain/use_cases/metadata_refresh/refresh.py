"""Atomic metadata refresh for a selected group of drives."""

from collections.abc import AsyncGenerator
from uuid import UUID

import anyio

from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataSourceError,
)
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    MetadataDriveRunPublication,
)
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import list_group_drives
from domain.use_cases.metadata_refresh.drive.refresh import (
    MetadataDriveIngestPrepared,
    MetadataDriveIngestRequest,
    discard_metadata_runs,
)
from domain.use_cases.metadata_refresh.drive.staging import stage_drive_metadata_refresh
from domain.use_cases.metadata_refresh.group._failures import build_failure_details
from domain.use_cases.metadata_refresh.group._models import (
    MetadataDriveIngestFailure,
    MetadataGroupIngestProgress,
    MetadataGroupIngestRequest,
    MetadataGroupIngestResult,
    MetadataGroupIngestSkipped,
    MetadataGroupIngestStarted,
)
from domain.use_cases.metadata_refresh.group._selectors import (
    build_selector_rules,
    validate_selector_conflicts,
)


async def _stage_and_publish_group_metadata(
    user_id: str,
    request: MetadataGroupIngestRequest,
    drive_source: GroupDriveSource,
    metadata_source: DocumentRefreshSource,
    store: DocumentRefreshStore,
    operation_id: UUID,
) -> AsyncGenerator[
    MetadataGroupIngestProgress | MetadataGroupIngestSkipped | MetadataGroupIngestResult
]:
    """Stage selected drives and publish them together after all scans succeed.

    A staging or publication failure discards every prepared drive run, so the
    group does not expose a partial refresh.
    """
    (
        drive_requests,
        has_explicit_include_drive_selector,
    ) = await _select_atomic_refresh_drives(
        user_id, request, drive_source, operation_id
    )
    processed_document_count = 0
    skipped_drive_ids: list[str] = []
    drive_publications: list[MetadataDriveRunPublication] = []
    try:
        for drive_request in drive_requests:
            drive_events = stage_drive_metadata_refresh(
                user_id,
                drive_request,
                metadata_source,
                store,
                allow_skip=not has_explicit_include_drive_selector,
            )
            try:
                async for event in drive_events:
                    match event:
                        case MetadataGroupIngestSkipped(drive_id=drive_id, reason=reason):
                            skipped_drive_ids.append(drive_id)
                            yield MetadataGroupIngestSkipped(
                                drive_id, reason, processed_document_count
                            )
                        case MetadataDriveIngestPrepared(publication=publication):
                            drive_publications.append(publication)
                        case MetadataGroupIngestProgress(
                            drive_id=drive_id, progress=drive_progress
                        ):
                            processed_document_count += 1
                            yield MetadataGroupIngestProgress(
                                drive_id,
                                drive_progress,
                                processed_document_count,
                            )
            finally:
                with anyio.CancelScope(shield=True):
                    await drive_events.aclose()

        await store.publish_metadata_runs(
            tuple(drive_publications),
            operation_id,
            {
                "processed": processed_document_count,
                "skipped_drive_ids": skipped_drive_ids,
            },
            {"group_id": request.group_id},
        )
    except BaseException:
        await discard_metadata_runs(
            store,
            tuple(publication.run_id for publication in drive_publications),
        )
        raise

    yield MetadataGroupIngestResult(processed_document_count, tuple(skipped_drive_ids))


async def _select_atomic_refresh_drives(
    user_id: str,
    request: MetadataGroupIngestRequest,
    drive_source: GroupDriveSource,
    operation_id: UUID,
) -> tuple[list[MetadataDriveIngestRequest], bool]:
    group = await list_group_drives(user_id, request.group_id, drive_source)
    drive_locations_by_id = {
        location.drive_id: location for location in group.locations
    }
    available_drive_ids = set(drive_locations_by_id)
    included_rules = build_selector_rules(request.include)
    excluded_rules = build_selector_rules(request.exclude)
    included_drive_ids = set(included_rules)
    excluded_drive_ids = set(excluded_rules)
    unavailable_included = [
        drive_id
        for drive_id in included_drive_ids
        if drive_id not in available_drive_ids
    ]
    if unavailable_included:
        raise MetadataDriveIngestFailure(
            unavailable_included[0],
            MetadataSourceError(403, "Included drive is not accessible in this group"),
        )
    unknown_excluded = excluded_drive_ids - available_drive_ids
    if unknown_excluded:
        raise InvalidMetadataSelection(
            "drive IDs are not available in this group: "
            + ", ".join(sorted(unknown_excluded))
        )
    if included_drive_ids and excluded_drive_ids - included_drive_ids:
        raise InvalidMetadataSelection("excluded drives must also be included")
    validate_selector_conflicts(included_rules, excluded_rules)
    selected_drive_ids = tuple(
        drive_id
        for drive_id in group.drive_ids
        if (not included_drive_ids or drive_id in included_drive_ids)
        and (
            drive_id not in excluded_drive_ids
            or not any(
                not selector.paths and not selector.extensions
                for selector in request.exclude
                if selector.drive_id == drive_id
            )
        )
    )
    # Construct every per-drive request before the first write.
    drive_requests = [
        MetadataDriveIngestRequest(
            drive_id,
            root_item_id=drive_locations_by_id[drive_id].root_item_id,
            operation_id=operation_id,
            include_rules=included_rules.get(drive_id, ()),
            exclude_rules=excluded_rules.get(drive_id, ()),
        )
        for drive_id in selected_drive_ids
    ]
    return drive_requests, bool(included_drive_ids)


async def refresh_group_metadata_atomically(
    user_id: str,
    request: MetadataGroupIngestRequest,
    drive_source: GroupDriveSource,
    metadata_source: DocumentRefreshSource,
    store: DocumentRefreshStore,
) -> AsyncGenerator[
    MetadataGroupIngestStarted
    | MetadataGroupIngestProgress
    | MetadataGroupIngestSkipped
    | MetadataGroupIngestResult
]:
    """Refresh all selected group drives as one atomic metadata operation.

    Selectors are validated before an operation is created. Inaccessible drives
    may be skipped only when there is no explicit include-drive selector.

    Args:
        user_id: User whose membership grants access to the group.
        request: Group ID and include/exclude drive selectors.
        drive_source: Source of accessible group drive locations.
        metadata_source: Source used to scan drive metadata.
        store: Store used to stage, publish, and record the operation.

    Yields:
        A started event, document progress, optional skipped-drive events, and a
        result after all prepared runs have been published.
    """
    # Normalize and reject malformed or ambiguous selectors before creating a run.
    included_rules = build_selector_rules(request.include)
    excluded_rules = build_selector_rules(request.exclude)
    validate_selector_conflicts(included_rules, excluded_rules)
    operation_id = await store.begin_group_operation(user_id, request.group_id)
    refresh_events = _stage_and_publish_group_metadata(
        user_id, request, drive_source, metadata_source, store, operation_id
    )
    processed_document_count = 0
    skipped_drive_ids: list[str] = []
    try:
        yield MetadataGroupIngestStarted(operation_id)
        async for event in refresh_events:
            match event:
                case MetadataGroupIngestProgress(processed=processed_count):
                    processed_document_count = processed_count
                case MetadataGroupIngestSkipped(drive_id=drive_id):
                    skipped_drive_ids.append(drive_id)
                case MetadataGroupIngestResult(
                    processed=final_processed_count,
                    skipped_drive_ids=final_skipped_drive_ids,
                ):
                    processed_document_count = final_processed_count
                    skipped_drive_ids = list(final_skipped_drive_ids)
            yield event
    except BaseException as exc:
        cancelled = isinstance(exc, anyio.get_cancelled_exc_class() | GeneratorExit)
        failure_code = "request_interrupted"
        summary: dict[str, object] = {
            "processed": processed_document_count,
            "skipped_drive_ids": skipped_drive_ids,
        }
        if not cancelled and isinstance(exc, Exception):
            failure_code, summary = build_failure_details(
                exc, processed_document_count, skipped_drive_ids
            )
        with anyio.CancelScope(shield=True):
            await store.fail_group_operation(
                operation_id,
                failure_code,
                interrupted=cancelled,
                summary=summary,
            )
        raise
    finally:
        with anyio.CancelScope(shield=True):
            await refresh_events.aclose()
