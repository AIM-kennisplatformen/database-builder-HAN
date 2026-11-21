from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from uuid import UUID

import anyio

from domain.exceptions.metadata_ingest_errors import (
    InvalidMetadataSelection,
    MetadataRunAlreadyActive,
    MetadataSourceError,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    normalize_metadata_rules,
)
from domain.ports.documents.document_metadata_source import DocumentRefreshSource
from domain.ports.documents.document_store import (
    DocumentRefreshStore,
    MetadataRunPublication,
)
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import GroupNotAccessible, get_group_drives
from domain.use_cases.metadata_ingest.drive import (
    MetadataIngestDriveProgress,
    MetadataIngestDriveRequest,
    MetadataIngestPrepared,
    discard_metadata_runs,
    prepare_metadata,
)


@dataclass(frozen=True)
class DriveMetadataSelector:
    drive_id: str
    paths: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)


def _selector_rules(
    selectors: list[DriveMetadataSelector],
) -> dict[str, tuple[MetadataPathRule, ...]]:
    grouped: dict[str, list[MetadataPathRule]] = {}
    for selector in selectors:
        if not selector.drive_id.strip():
            raise InvalidMetadataSelection("drive_id is required")
        if selector.paths and any(
            not path.replace("\\", "/").strip("/") for path in selector.paths
        ):
            raise InvalidMetadataSelection(
                "paths must contain folder paths relative to the scan root"
            )
        paths = selector.paths or [""]
        normalized_rules = normalize_metadata_rules(
            tuple(MetadataPathRule(path, tuple(selector.extensions)) for path in paths),
            "paths",
        )
        rules = grouped.setdefault(selector.drive_id, [])
        rules.extend(normalized_rules)
    return {
        drive_id: normalize_metadata_rules(tuple(rules), "selectors")
        for drive_id, rules in grouped.items()
    }


def _validate_selector_conflicts(
    include_rules: dict[str, tuple[MetadataPathRule, ...]],
    exclude_rules: dict[str, tuple[MetadataPathRule, ...]],
) -> None:
    for drive_id in include_rules.keys() & exclude_rules.keys():
        for include in include_rules[drive_id]:
            for exclude in exclude_rules[drive_id]:
                if include.path != exclude.path:
                    continue
                if (
                    not include.extensions
                    or not exclude.extensions
                    or set(include.extensions) & set(exclude.extensions)
                ):
                    raise InvalidMetadataSelection(
                        f"include and exclude selectors overlap for drive {drive_id} at {include.path or '/'}"
                    )


@dataclass(frozen=True)
class MetadataIngestRequest:
    group_id: str
    include: list[DriveMetadataSelector] = field(default_factory=list)
    exclude: list[DriveMetadataSelector] = field(default_factory=list)


@dataclass(frozen=True)
class MetadataIngestProgress:
    drive_id: str
    update: MetadataIngestDriveProgress
    processed: int = 0


@dataclass(frozen=True)
class MetadataIngestSkipped:
    drive_id: str
    reason: str
    processed: int = 0


@dataclass(frozen=True)
class MetadataIngestResult:
    processed: int
    skipped_drive_ids: tuple[str, ...]


@dataclass(frozen=True)
class MetadataIngestStarted:
    operation_id: UUID


class MetadataIngestDriveFailure(Exception):
    def __init__(self, drive_id: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.drive_id: str = drive_id
        self.cause: Exception = cause


def _failure_details(
    exc: Exception,
    processed: int,
    skipped_drive_ids: list[str],
) -> tuple[str, dict[str, object]]:
    summary: dict[str, object] = {
        "processed": processed,
        "skipped_drive_ids": skipped_drive_ids,
    }
    cause = exc
    if isinstance(exc, MetadataIngestDriveFailure):
        summary["failed_drive_id"] = exc.drive_id
        cause = exc.cause

    if isinstance(cause, MetadataSourceError):
        summary["source_status"] = cause.status_code
        summary["reason"] = (
            "Drive is not accessible"
            if cause.status_code == 403
            else "Metadata source request failed"
        )
        return "metadata.source_error", summary
    if isinstance(cause, MetadataRunAlreadyActive):
        summary["reason"] = "A metadata run is already active"
        return "metadata.run_already_active", summary
    if isinstance(cause, GroupNotAccessible):
        summary["reason"] = "Group is not accessible"
        return "metadata.group_not_accessible", summary
    if isinstance(cause, InvalidMetadataSelection):
        summary["reason"] = "Metadata selection is invalid"
        return "metadata.invalid_selection", summary
    if isinstance(cause, SourceUnavailable):
        summary["reason"] = "Metadata source is unavailable"
        return "metadata.source_unavailable", summary
    if isinstance(cause, StoreUnavailable):
        summary["reason"] = "Metadata store is unavailable"
        return "metadata.store_unavailable", summary

    summary["reason"] = "Metadata ingestion failed"
    return "metadata.ingest_failed", summary


async def _ingest_drive(
    user_id: str,
    request: MetadataIngestDriveRequest,
    metadata_source: DocumentRefreshSource,
    store: DocumentRefreshStore,
    *,
    allow_skip: bool,
) -> AsyncGenerator[
    MetadataIngestProgress | MetadataIngestSkipped | MetadataIngestPrepared
]:
    updates = prepare_metadata(user_id, request, metadata_source, store)
    try:
        try:
            async for update in updates:
                if isinstance(update, MetadataIngestPrepared):
                    yield update
                else:
                    yield MetadataIngestProgress(request.drive_id, update)
        except MetadataSourceError as exc:
            if exc.status_code == 403 and allow_skip:
                yield MetadataIngestSkipped(request.drive_id, str(exc))
                return
            raise MetadataIngestDriveFailure(request.drive_id, exc) from exc
        except Exception as exc:
            raise MetadataIngestDriveFailure(request.drive_id, exc) from exc
    finally:
        # Propagate request cancellation through the per-drive use case.
        with anyio.CancelScope(shield=True):
            await updates.aclose()


async def _ingest_group_metadata(
    user_id: str,
    request: MetadataIngestRequest,
    drive_source: GroupDriveSource,
    metadata_source: DocumentRefreshSource,
    store: DocumentRefreshStore,
    operation_id: UUID,
) -> AsyncGenerator[
    MetadataIngestProgress | MetadataIngestSkipped | MetadataIngestResult
]:
    """Validate the selection, then ingest selected drives in discovery order."""
    group = await get_group_drives(user_id, request.group_id, drive_source)
    locations = {location.drive_id: location for location in group.locations}
    available = set(locations)
    included_rules = _selector_rules(request.include)
    excluded_rules = _selector_rules(request.exclude)
    included = set(included_rules)
    excluded = set(excluded_rules)
    unavailable_included = [
        drive_id for drive_id in included if drive_id not in available
    ]
    if unavailable_included:
        raise MetadataIngestDriveFailure(
            unavailable_included[0],
            MetadataSourceError(403, "Included drive is not accessible in this group"),
        )
    unknown_excluded = excluded - available
    if unknown_excluded:
        raise InvalidMetadataSelection(
            "drive IDs are not available in this group: "
            + ", ".join(sorted(unknown_excluded))
        )
    if included and excluded - included:
        raise InvalidMetadataSelection("excluded drives must also be included")
    _validate_selector_conflicts(included_rules, excluded_rules)
    selected = tuple(
        drive_id
        for drive_id in group.drive_ids
        if (not included or drive_id in included)
        and (
            drive_id not in excluded
            or not any(
                not selector.paths and not selector.extensions
                for selector in request.exclude
                if selector.drive_id == drive_id
            )
        )
    )
    # Construct every per-drive request before the first write.
    requests = [
        MetadataIngestDriveRequest(
            drive_id,
            root_item_id=locations[drive_id].root_item_id,
            operation_id=operation_id,
            include_rules=included_rules.get(drive_id, ()),
            exclude_rules=excluded_rules.get(drive_id, ()),
        )
        for drive_id in selected
    ]
    processed = 0
    skipped_drive_ids: list[str] = []
    publications: list[MetadataRunPublication] = []
    try:
        for drive_request in requests:
            updates = _ingest_drive(
                user_id,
                drive_request,
                metadata_source,
                store,
                allow_skip=not included,
            )
            try:
                async for update in updates:
                    if isinstance(update, MetadataIngestSkipped):
                        skipped_drive_ids.append(update.drive_id)
                        yield MetadataIngestSkipped(
                            update.drive_id,
                            update.reason,
                            processed,
                        )
                    elif isinstance(update, MetadataIngestPrepared):
                        publications.append(update.publication)
                    else:
                        processed += 1
                        yield MetadataIngestProgress(
                            update.drive_id,
                            update.update,
                            processed,
                        )
            finally:
                with anyio.CancelScope(shield=True):
                    await updates.aclose()

        await store.publish_metadata_runs(
            tuple(publications),
            operation_id,
            {"processed": processed, "skipped_drive_ids": skipped_drive_ids},
            {"group_id": request.group_id},
        )
    except BaseException:
        await discard_metadata_runs(
            store, tuple(publication.run_id for publication in publications)
        )
        raise

    yield MetadataIngestResult(processed, tuple(skipped_drive_ids))


async def ingest_group_metadata(
    user_id: str,
    request: MetadataIngestRequest,
    drive_source: GroupDriveSource,
    metadata_source: DocumentRefreshSource,
    store: DocumentRefreshStore,
) -> AsyncGenerator[
    MetadataIngestStarted
    | MetadataIngestProgress
    | MetadataIngestSkipped
    | MetadataIngestResult
]:
    # Normalize and reject malformed or ambiguous selectors before creating a run.
    included_rules = _selector_rules(request.include)
    excluded_rules = _selector_rules(request.exclude)
    _validate_selector_conflicts(included_rules, excluded_rules)
    operation_id = await store.begin_group_operation(user_id, request.group_id)
    updates = _ingest_group_metadata(
        user_id, request, drive_source, metadata_source, store, operation_id
    )
    processed = 0
    skipped_drive_ids: list[str] = []
    try:
        yield MetadataIngestStarted(operation_id)
        async for update in updates:
            if isinstance(update, MetadataIngestProgress):
                processed = update.processed
            elif isinstance(update, MetadataIngestSkipped):
                skipped_drive_ids.append(update.drive_id)
            yield update
    except BaseException as exc:
        cancelled = isinstance(exc, anyio.get_cancelled_exc_class() | GeneratorExit)
        failure_code = "request_interrupted"
        summary: dict[str, object] = {
            "processed": processed,
            "skipped_drive_ids": skipped_drive_ids,
        }
        if not cancelled and isinstance(exc, Exception):
            failure_code, summary = _failure_details(exc, processed, skipped_drive_ids)
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
            await updates.aclose()
