"""Refresh and publish each selected group drive independently."""

import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from uuid import UUID

import anyio

from domain.exceptions.error import OperationFailure
from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataSourceError,
)
from domain.models.documents.metadata_selection import MetadataPathRule
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import (
    CombinedDocumentRefreshStore,
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
    MetadataDrivePublished,
    MetadataDriveRefreshFailure,
    MetadataGroupIngestProgress,
    MetadataGroupIngestRequest,
    MetadataGroupIngestSkipped,
    MetadataGroupIngestStarted,
    MetadataRefreshRunResult,
)
from domain.use_cases.metadata_refresh.group._selectors import (
    build_selector_rules,
    select_drive_ids,
    validate_selector_conflicts,
)

logger = logging.getLogger(__name__)


@dataclass
class _CombinedRefreshState:
    processed_document_count: int = 0
    failed_drive_ids: list[str] = dataclass_field(default_factory=list)
    skipped_drive_ids: list[str] = dataclass_field(default_factory=list)
    active_drive_id: str | None = None
    prepared_publication: MetadataDriveRunPublication | None = None
    drive_event_stream: (
        AsyncGenerator[
            MetadataGroupIngestProgress | MetadataGroupIngestSkipped | MetadataDriveIngestPrepared
        ]
        | None
    ) = None
    drive_processed_document_count: int = 0


async def _select_independent_refresh_drives(
    user_id: str,
    request: MetadataGroupIngestRequest,
    drive_source: GroupDriveSource,
    operation_id: UUID,
    included_rules: dict[str, tuple[MetadataPathRule, ...]],
    excluded_rules: dict[str, tuple[MetadataPathRule, ...]],
    validation_include_drive_ids: frozenset[str] | None,
    validation_exclude_drive_ids: frozenset[str] | None,
) -> tuple[list[MetadataDriveIngestRequest], bool]:
    group = await list_group_drives(user_id, request.group_id, drive_source)
    drive_locations_by_id = {
        location.drive_id: location for location in group.locations
    }
    available_drive_ids = set(drive_locations_by_id)
    included_drive_ids = set(
        validation_include_drive_ids
        if validation_include_drive_ids is not None
        else included_rules
    )
    excluded_drive_ids = set(excluded_rules)
    unavailable_included = included_drive_ids - available_drive_ids
    if unavailable_included:
        raise MetadataDriveIngestFailure(
            next(iter(unavailable_included)),
            MetadataSourceError(403, "Included drive is not accessible in this group"),
        )
    validation_excluded = set(validation_exclude_drive_ids or ())
    unknown_excluded = (excluded_drive_ids | validation_excluded) - available_drive_ids
    if unknown_excluded:
        raise InvalidMetadataSelection(
            "drive IDs are not available in this group: "
            + ", ".join(sorted(unknown_excluded))
        )
    if (
        included_drive_ids
        and (excluded_drive_ids | validation_excluded) - included_drive_ids
    ):
        raise InvalidMetadataSelection("excluded drives must also be included")
    drive_ids_to_refresh = select_drive_ids(
        group.drive_ids, included_drive_ids, excluded_drive_ids, request.exclude
    )
    drive_requests = [
        MetadataDriveIngestRequest(
            drive_id,
            include_rules=included_rules.get(drive_id, ()),
            exclude_rules=excluded_rules.get(drive_id, ()),
            root_item_id=drive_locations_by_id[drive_id].root_item_id,
            operation_id=operation_id,
        )
        for drive_id in drive_ids_to_refresh
    ]
    return (
        drive_requests,
        bool(included_drive_ids),
    )


async def _forward_drive_refresh_events(
    drive_event_stream: AsyncGenerator[
        MetadataGroupIngestProgress | MetadataGroupIngestSkipped | MetadataDriveIngestPrepared
    ],
    state: _CombinedRefreshState,
) -> AsyncGenerator[
    MetadataGroupIngestProgress | MetadataGroupIngestSkipped | MetadataDriveIngestPrepared
]:
    async for event in drive_event_stream:
        match event:
            case MetadataDriveIngestPrepared() | MetadataGroupIngestSkipped():
                yield event
            case MetadataGroupIngestProgress(drive_id=drive_id, progress=drive_progress):
                state.processed_document_count += 1
                state.drive_processed_document_count += 1
                yield MetadataGroupIngestProgress(
                    drive_id, drive_progress, state.processed_document_count
                )


async def _record_drive_refresh_failure(
    store: CombinedDocumentRefreshStore,
    operation_id: UUID,
    drive_id: str,
    exc: MetadataDriveIngestFailure,
    state: _CombinedRefreshState,
) -> MetadataDriveRefreshFailure:
    failure_code, failure_summary = build_failure_details(
        exc, state.processed_document_count, state.skipped_drive_ids
    )
    detail = str(failure_summary.get("reason", str(exc)))
    with anyio.CancelScope(shield=True):
        await store.record_metadata_drive_outcome(
            operation_id,
            drive_id,
            {
                "status": "failed",
                "failure_code": failure_code,
                "detail": detail,
                "processed": state.drive_processed_document_count,
            },
        )
        state.failed_drive_ids.append(drive_id)
        state.active_drive_id = None
    return MetadataDriveRefreshFailure(
        drive_id, "failed", failure_code, detail, state.processed_document_count
    )


async def _record_skipped_drive(
    store: CombinedDocumentRefreshStore,
    operation_id: UUID,
    drive_id: str,
    state: _CombinedRefreshState,
) -> MetadataDriveRefreshFailure:
    detail = "Drive is not accessible"
    with anyio.CancelScope(shield=True):
        await store.record_metadata_drive_outcome(
            operation_id,
            drive_id,
            {
                "status": "skipped",
                "failure_code": "metadata.drive_not_accessible",
                "detail": detail,
                "processed": state.drive_processed_document_count,
            },
        )
        state.skipped_drive_ids.append(drive_id)
        state.active_drive_id = None
    return MetadataDriveRefreshFailure(
        drive_id,
        "skipped",
        "metadata.drive_not_accessible",
        detail,
        state.processed_document_count,
    )


async def _publish_refreshed_drive(
    store: CombinedDocumentRefreshStore,
    operation_id: UUID,
    drive_id: str,
    publication: MetadataDriveRunPublication,
    state: _CombinedRefreshState,
) -> MetadataDrivePublished:
    with anyio.CancelScope(shield=True):
        await store.publish_metadata_drive(
            operation_id,
            drive_id,
            publication,
            {
                "status": "published",
                "metadata_run_id": str(publication.run_id),
                "processed": state.drive_processed_document_count,
            },
        )
        state.active_drive_id = None
        state.prepared_publication = None
    return MetadataDrivePublished(
        drive_id, publication.run_id, state.drive_processed_document_count
    )


async def _refresh_drive_metadata_independently(
    user_id: str,
    drive_request: MetadataDriveIngestRequest,
    has_explicit_include_drive_selector: bool,
    capture_relative_paths: bool,
    operation_id: UUID,
    metadata_source: DocumentRefreshSource,
    store: CombinedDocumentRefreshStore,
    state: _CombinedRefreshState,
) -> AsyncGenerator[
    MetadataGroupIngestProgress | MetadataDriveRefreshFailure | MetadataDrivePublished
]:
    drive_id = drive_request.drive_id
    state.active_drive_id = drive_id
    state.prepared_publication = None
    state.drive_processed_document_count = 0
    await store.record_metadata_drive_outcome(
        operation_id, drive_id, {"status": "refreshing", "processed": 0}
    )
    drive_event_stream = stage_drive_metadata_refresh(
        user_id,
        drive_request,
        metadata_source,
        store,
        allow_skip=not has_explicit_include_drive_selector,
        capture_relative_paths=capture_relative_paths,
    )
    state.drive_event_stream = drive_event_stream
    skipped: MetadataGroupIngestSkipped | None = None
    try:
        try:
            async for event in _forward_drive_refresh_events(drive_event_stream, state):
                match event:
                    case MetadataDriveIngestPrepared(publication=publication):
                        state.prepared_publication = publication
                    case MetadataGroupIngestSkipped():
                        skipped = event
                    case MetadataGroupIngestProgress():
                        yield event
        except MetadataDriveIngestFailure as exc:
            yield await _record_drive_refresh_failure(store, operation_id, drive_id, exc, state)
            return
    finally:
        with anyio.CancelScope(shield=True):
            await drive_event_stream.aclose()
        state.drive_event_stream = None

    if skipped is not None:
        yield await _record_skipped_drive(store, operation_id, drive_id, state)
        return
    publication = state.prepared_publication
    if publication is None:
        raise RuntimeError("Metadata drive refresh ended without an outcome")
    yield await _publish_refreshed_drive(
        store, operation_id, drive_id, publication, state
    )


async def _refresh_requested_drives(
    user_id: str,
    drive_requests: list[MetadataDriveIngestRequest],
    has_explicit_include_drive_selector: bool,
    capture_relative_paths_drive_ids: frozenset[str] | None,
    operation_id: UUID,
    metadata_source: DocumentRefreshSource,
    store: CombinedDocumentRefreshStore,
    state: _CombinedRefreshState,
) -> AsyncGenerator[
    MetadataGroupIngestProgress | MetadataDriveRefreshFailure | MetadataDrivePublished
]:
    for drive_request in drive_requests:
        drive_refresh_events = _refresh_drive_metadata_independently(
            user_id,
            drive_request,
            has_explicit_include_drive_selector,
            drive_request.drive_id in (capture_relative_paths_drive_ids or ()),
            operation_id,
            metadata_source,
            store,
            state,
        )
        try:
            async for event in drive_refresh_events:
                yield event
        finally:
            with anyio.CancelScope(shield=True):
                await drive_refresh_events.aclose()


async def _fail_combined_refresh(
    exc: BaseException,
    operation_id: UUID,
    store: CombinedDocumentRefreshStore,
    state: _CombinedRefreshState,
) -> None:
    cancelled = isinstance(exc, anyio.get_cancelled_exc_class() | GeneratorExit)
    failure_code = "request_interrupted"
    summary: dict[str, object] = {
        "processed": state.processed_document_count,
        "failed_drive_ids": state.failed_drive_ids,
        "skipped_drive_ids": state.skipped_drive_ids,
    }
    if not cancelled and isinstance(exc, OperationFailure):
        failure_code = exc.failure_code
        summary.update(exc.failure_summary or {})
    elif not cancelled and isinstance(exc, Exception):
        failure_code, failure_summary = build_failure_details(
            exc, state.processed_document_count, state.skipped_drive_ids
        )
        summary.update(failure_summary)
    with anyio.CancelScope(shield=True):
        if state.prepared_publication is not None:
            await discard_metadata_runs(store, (state.prepared_publication.run_id,))
        if state.active_drive_id is not None:
            await _record_active_drive_failure(
                operation_id, store, state, failure_code, cancelled
            )
        await store.fail_group_operation(
            operation_id,
            failure_code,
            interrupted=cancelled,
            summary=summary,
        )


async def _record_active_drive_failure(
    operation_id: UUID,
    store: CombinedDocumentRefreshStore,
    state: _CombinedRefreshState,
    failure_code: str,
    cancelled: bool,
) -> None:
    drive_id = state.active_drive_id
    if drive_id is None:
        return
    try:
        await store.record_metadata_drive_outcome(
            operation_id,
            drive_id,
            {
                "status": "interrupted" if cancelled else "failed",
                "failure_code": failure_code,
                "processed": state.processed_document_count,
            },
        )
    except Exception:
        logger.exception(
            "Could not persist interrupted metadata drive outcome for %s",
            drive_id,
        )


async def refresh_group_metadata_per_drive(
    user_id: str,
    request: MetadataGroupIngestRequest,
    drive_source: GroupDriveSource,
    metadata_source: DocumentRefreshSource,
    store: CombinedDocumentRefreshStore,
    *,
    validation_include_drive_ids: frozenset[str] | None = None,
    validation_exclude_drive_ids: frozenset[str] | None = None,
    capture_relative_paths_drive_ids: frozenset[str] | None = None,
) -> AsyncGenerator[
    MetadataGroupIngestStarted
    | MetadataGroupIngestProgress
    | MetadataDrivePublished
    | MetadataDriveRefreshFailure
    | MetadataRefreshRunResult
]:
    """Refresh and publish each selected drive independently for document work."""
    included_rules = build_selector_rules(request.include)
    excluded_rules = build_selector_rules(request.exclude)
    validate_selector_conflicts(included_rules, excluded_rules)
    operation_id = await store.begin_group_operation(user_id, request.group_id)
    state = _CombinedRefreshState()
    try:
        yield MetadataGroupIngestStarted(operation_id)
        (
            drive_requests,
            has_explicit_include_drive_selector,
        ) = await _select_independent_refresh_drives(
            user_id,
            request,
            drive_source,
            operation_id,
            included_rules,
            excluded_rules,
            validation_include_drive_ids,
            validation_exclude_drive_ids,
        )
        async for event in _refresh_requested_drives(
            user_id,
            drive_requests,
            has_explicit_include_drive_selector,
            capture_relative_paths_drive_ids,
            operation_id,
            metadata_source,
            store,
            state,
        ):
            yield event

        await store.complete_group_operation(
            operation_id,
            {
                "processed": state.processed_document_count,
                "failed_drive_ids": state.failed_drive_ids,
                "skipped_drive_ids": state.skipped_drive_ids,
            },
        )
        yield MetadataRefreshRunResult(
            state.processed_document_count,
            tuple(state.failed_drive_ids),
            tuple(state.skipped_drive_ids),
        )
    except BaseException as exc:
        await _fail_combined_refresh(exc, operation_id, store, state)
        raise
    finally:
        if state.drive_event_stream is not None:
            with anyio.CancelScope(shield=True):
                await state.drive_event_stream.aclose()
