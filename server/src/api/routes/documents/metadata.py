from collections.abc import AsyncIterator
from enum import StrEnum
from traceback import format_exception
from typing import Annotated, ClassVar

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field

from api.progress import ProgressEventLimiter
from config import config
from dependencies import (
    get_current_graph_user_id,
    get_document_metadata_source,
    get_document_store,
    get_group_drive_source,
)
from domain.exceptions.metadata_inventory_errors import (
    MetadataRunAlreadyActive,
    MetadataSourceError,
    SourceUnavailable,
    StoreUnavailable,
)
from domain.ports.documents.document_metadata_source import DocumentMetadataSource
from domain.ports.documents.document_store import DocumentStore
from domain.ports.documents.group_drive_source import GroupDriveSource
from domain.use_cases.group_drive_discovery import GroupNotAccessible
from domain.use_cases.group_metadata_inventory import (
    DriveInventoryFailure,
    DriveMetadataSelector,
    GroupMetadataRequest,
    GroupMetadataSkipped,
    inventory_group_metadata,
)

router = APIRouter()

type InventoryError = (
    ValueError
    | MetadataSourceError
    | SourceUnavailable
    | StoreUnavailable
    | RuntimeError
    | MetadataRunAlreadyActive
)


class DriveMetadataSelectorBody(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    drive_id: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)


class InventoryMetadataRequestBody(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    include: list[DriveMetadataSelectorBody] = Field(
        default_factory=list,
        description="Drive, folder, and file-type selectors; empty selects every drive",
    )
    exclude: list[DriveMetadataSelectorBody] = Field(
        default_factory=list,
        description="Drive, folder, and file-type exclusions",
    )


class InventoryMetadataEventStatus(StrEnum):
    PROGRESS = "progress"
    SKIPPED = "skipped"
    COMPLETE = "complete"
    ERROR = "error"


class InventoryMetadataEvent(BaseModel):
    status: InventoryMetadataEventStatus
    group_id: str
    processed: int
    drive_id: str | None = None
    total: int | None = None
    available: int | None = None
    item_id: str | None = None
    name: str | None = None
    error_status: int | None = None
    skipped_drive_ids: list[str] | None = None
    detail: str | None = None
    traceback: str | None = None


def _sse_event(event: InventoryMetadataEvent) -> ServerSentEvent:
    return ServerSentEvent(
        event=event.status.value, data=event.model_dump(exclude_none=True)
    )


def _error_status(exc: InventoryError) -> int:
    if isinstance(exc, MetadataRunAlreadyActive):
        return 409
    if isinstance(exc, GroupNotAccessible):
        return 404
    if isinstance(exc, ValueError):
        return 422
    if isinstance(exc, MetadataSourceError):
        return exc.status_code if 400 <= exc.status_code < 500 else 502
    if isinstance(exc, (SourceUnavailable, StoreUnavailable)):
        return 503
    return 500


def _failure_event(
    exc: InventoryError,
    group_id: str,
    drive_id: str | None,
    processed: int,
    *,
    traceback_exc: BaseException | None = None,
) -> ServerSentEvent:
    return _sse_event(
        InventoryMetadataEvent(
            status=InventoryMetadataEventStatus.ERROR,
            group_id=group_id,
            drive_id=drive_id,
            processed=processed,
            error_status=_error_status(exc),
            detail=str(exc),
            traceback="".join(format_exception(traceback_exc or exc))
            if config.debug
            else None,
        )
    )


@router.post(
    "/groups/{group_id}/metadata",
    response_class=EventSourceResponse,
    summary="Inventory metadata from a member group's selected drives",
)
async def inventory_metadata_endpoint(
    group_id: str,
    request: InventoryMetadataRequestBody,
    user_id: Annotated[str, Depends(get_current_graph_user_id)],
    drive_source: Annotated[GroupDriveSource, Depends(get_group_drive_source)],
    source: Annotated[DocumentMetadataSource, Depends(get_document_metadata_source)],
    store: Annotated[DocumentStore, Depends(get_document_store)],
) -> AsyncIterator[ServerSentEvent]:
    processed = 0
    skipped_drive_ids: list[str] = []
    progress_limiter = ProgressEventLimiter(config.stream_progress_interval_seconds)
    selection = GroupMetadataRequest(
        group_id,
        [
            DriveMetadataSelector(
                selector.drive_id, selector.paths, selector.extensions
            )
            for selector in request.include
        ],
        [
            DriveMetadataSelector(
                selector.drive_id, selector.paths, selector.extensions
            )
            for selector in request.exclude
        ],
    )
    try:
        async for progress in inventory_group_metadata(
            user_id, selection, drive_source, source, store
        ):
            if isinstance(progress, GroupMetadataSkipped):
                skipped_drive_ids.append(progress.drive_id)
                yield _sse_event(
                    InventoryMetadataEvent(
                        status=InventoryMetadataEventStatus.SKIPPED,
                        group_id=group_id,
                        drive_id=progress.drive_id,
                        processed=processed,
                        error_status=403,
                        detail=progress.reason,
                    )
                )
                continue
            processed += 1
            if not progress_limiter.should_emit():
                continue
            update = progress.update
            yield _sse_event(
                InventoryMetadataEvent(
                    status=InventoryMetadataEventStatus.PROGRESS,
                    group_id=group_id,
                    drive_id=progress.drive_id,
                    processed=processed,
                    total=update.total,
                    available=update.available,
                    item_id=update.document.id.id[1],
                    name=update.document.file_metadata.name,
                )
            )
    except DriveInventoryFailure as exc:
        cause = exc.cause
        if isinstance(
            cause,
            (
                ValueError,
                MetadataSourceError,
                MetadataRunAlreadyActive,
                SourceUnavailable,
                StoreUnavailable,
            ),
        ):
            yield _failure_event(cause, group_id, exc.drive_id, processed)
        else:
            yield _failure_event(
                RuntimeError("Metadata inventory failed"),
                group_id,
                exc.drive_id,
                processed,
                traceback_exc=cause,
            )
    except (
        ValueError,
        MetadataSourceError,
        MetadataRunAlreadyActive,
        SourceUnavailable,
        StoreUnavailable,
    ) as exc:
        yield _failure_event(exc, group_id, None, processed)
    except Exception as exc:  # noqa: BLE001 - terminate the SSE stream on unexpected failures
        yield _failure_event(
            RuntimeError("Metadata inventory failed"),
            group_id,
            None,
            processed,
            traceback_exc=exc,
        )
    else:
        yield _sse_event(
            InventoryMetadataEvent(
                status=InventoryMetadataEventStatus.COMPLETE,
                group_id=group_id,
                processed=processed,
                skipped_drive_ids=skipped_drive_ids,
            )
        )
