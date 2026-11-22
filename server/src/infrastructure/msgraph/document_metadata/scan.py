"""Collect and filter a Graph delta snapshot for one metadata selection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.generated.drives.item.items.item.delta.delta_request_builder import (
    DeltaRequestBuilder,
)
from msgraph.graph_service_client import GraphServiceClient

from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.models.documents.document import Document
from domain.models.documents.metadata_selection import (
    MetadataSelection,
)
from infrastructure.msgraph.base import raise_for_graph_error, validate_graph_url
from infrastructure.msgraph.document_metadata.models import (
    GraphDeltaPage,
    GraphDriveItem,
)


@dataclass
class _DeltaSnapshot:
    matching_items: dict[str, GraphDriveItem] = field(default_factory=dict)
    folder_parents: dict[str, str | None] = field(default_factory=dict)
    folder_names: dict[str, str] = field(default_factory=dict)
    deleted_ids: set[str] = field(default_factory=set)
    delta_link: str | None = None
    baseline: bool = False


@dataclass
class ScanState:
    """Carry resolved scan options and Graph dependencies between scan helpers."""

    client: GraphServiceClient
    document_mapper: Callable[[str, GraphDriveItem, str | None], Document]
    drive_id: str
    selection: MetadataSelection
    root_item_id: str
    cursor: str | None
    path_scoped: bool
    capture_paths: bool
    root_id: str | None
    rule_resolve_folder_ids: dict[str, str]


def delta_path(root_item_id: str) -> str:
    if root_item_id == "root":
        return "root"
    return f"items/{quote(root_item_id, safe='')}"


def _parse_delta_response(
    response: Response, snapshot: _DeltaSnapshot
) -> GraphDeltaPage | None:
    """Parse a delta page, returning ``None`` only for an expired incremental cursor.

    Other Graph errors propagate. The caller is responsible for restarting from the
    baseline when this function returns ``None``.
    """
    try:
        raise_for_graph_error(response, "Graph rejected the drive metadata request")
    except MetadataSourceError as exc:
        if exc.status_code != 410 or snapshot.baseline:
            raise
        return None
    return GraphDeltaPage.model_validate_json(response.content)


async def read_delta(state: ScanState, baseline_url: str) -> _DeltaSnapshot:
    """Read all pages for a baseline or incremental delta scan.

    Args:
        state: Resolved scan options and Graph client dependencies.
        baseline_url: Delta URL to use for a baseline scan or expired-cursor retry.

    Returns:
        The accumulated file items, folder ancestry, deletions, and completed cursor.

    Raises:
        ValueError: If Graph repeats a page URL, omits a page, or fails to complete
            the delta sequence.
        MetadataSourceError: If Graph rejects the request and cannot be recovered by
            restarting from the baseline.
    """
    baseline = state.cursor is None or state.path_scoped or state.capture_paths
    snapshot = _DeltaSnapshot(baseline=baseline)
    next_url = baseline_url if baseline else state.cursor
    seen_urls: set[str] = set()
    delta: DeltaRequestBuilder = (
        state.client.drives.by_drive_id(state.drive_id)
        .items.by_drive_item_id(state.root_item_id)
        .delta
    )
    while next_url is not None:
        if next_url in seen_urls:
            raise ValueError("Graph repeated an @odata.nextLink")
        seen_urls.add(next_url)
        validate_graph_url(next_url)
        response = cast(
            Response | None,
            await delta.with_url(next_url).get(
                request_configuration=RequestConfiguration(
                    options=[ResponseHandlerOption(NativeResponseHandler())]
                )
            ),
        )
        if response is None:
            raise ValueError("Graph returned no drive item page")
        page = _parse_delta_response(response, snapshot)
        if page is None:
            snapshot = _DeltaSnapshot(baseline=True)
            next_url = baseline_url
            seen_urls.clear()
            continue
        snapshot = _apply_page(snapshot, page)
        next_url = page.next_link
        if next_url is None:
            if page.delta_link is None:
                raise ValueError("Graph did not complete the drive delta scan")
            snapshot.delta_link = page.delta_link
    return snapshot


def _apply_page(snapshot: _DeltaSnapshot, page: GraphDeltaPage) -> _DeltaSnapshot:
    for item in page.value:
        snapshot = _apply_item(snapshot, item)
    return snapshot


def _apply_item(snapshot: _DeltaSnapshot, item: GraphDriveItem) -> _DeltaSnapshot:
    if item.deleted is not None or item.folder is None:
        _ = snapshot.folder_parents.pop(item.id, None)
    else:
        snapshot.folder_parents[item.id] = (
            item.parent_reference.id if item.parent_reference else None
        )
        if item.name:
            snapshot.folder_names[item.id] = item.name
    excluded = item.deleted is not None or item.file is None or not item.name
    if item.deleted is not None or (excluded and not snapshot.baseline):
        snapshot.deleted_ids.add(item.id)
    else:
        snapshot.deleted_ids.discard(item.id)
    if excluded:
        _ = snapshot.matching_items.pop(item.id, None)
    else:
        snapshot.matching_items[item.id] = item
    return snapshot


def _ancestor_ids(
    state: ScanState, item: GraphDriveItem, snapshot: _DeltaSnapshot
) -> set[str]:
    parent_id = item.parent_reference.id if item.parent_reference else None
    found: set[str] = set()
    seen: set[str] = set()
    while parent_id is not None:
        if parent_id in seen:
            raise ValueError("Graph returned incomplete folder ancestry")
        seen.add(parent_id)
        found.add(parent_id)
        if parent_id == state.root_id:
            break
        if parent_id not in snapshot.folder_parents:
            raise ValueError("Graph returned incomplete folder ancestry")
        parent_id = snapshot.folder_parents[parent_id]
    if parent_id is None or (state.root_id is not None and state.root_id not in found):
        raise ValueError("Graph returned incomplete folder ancestry")
    return found


def _selected_by_rules(
    state: ScanState,
    item: GraphDriveItem,
    extension: str,
    snapshot: _DeltaSnapshot,
) -> bool:
    ancestors: set[str] = (
        _ancestor_ids(state, item, snapshot) if state.path_scoped else set()
    )
    candidates: list[tuple[int, bool]] = []
    for rules, included in (
        (state.selection.include_rules, True),
        (state.selection.exclude_rules, False),
    ):
        for rule in rules:
            rule_id = state.rule_resolve_folder_ids.get(rule.path, state.root_id)
            if rule_id is not None and rule_id not in ancestors:
                continue
            if rule.extensions and extension not in rule.extensions:
                continue
            candidates.append((rule.path.count("/") + bool(rule.path), included))
    if not candidates:
        return not state.selection.include_rules
    specificity = max(depth for depth, _ in candidates)
    return next(included for depth, included in candidates if depth == specificity)


async def _item_relative_path(
    state: ScanState,
    item: GraphDriveItem,
    snapshot: _DeltaSnapshot,
    known_folder_names: Mapping[str, str],
) -> tuple[str, dict[str, str]]:
    ancestors = _ancestor_ids(state, item, snapshot)
    parent_id = item.parent_reference.id if item.parent_reference else None
    ordered: list[str] = []
    while parent_id is not None and parent_id != state.root_id:
        ordered.append(parent_id)
        parent_id = snapshot.folder_parents.get(parent_id)
    ordered.reverse()
    names: list[str] = []
    resolved_names: dict[str, str] = {}
    for folder_id in ordered:
        name = known_folder_names.get(folder_id, snapshot.folder_names.get(folder_id))
        if name is None:
            response = cast(
                Response | None,
                await state.client.drives.by_drive_id(state.drive_id)
                .items.by_drive_item_id(folder_id)
                .get(
                    request_configuration=RequestConfiguration(
                        options=[ResponseHandlerOption(NativeResponseHandler())]
                    )
                ),
            )
            if response is None:
                raise ValueError("Graph returned no folder metadata")
            raise_for_graph_error(response, "Graph rejected folder ancestry")
            folder = GraphDriveItem.model_validate_json(response.content)
            if folder.folder is None or not folder.name:
                raise ValueError("Graph returned incomplete folder ancestry")
            name = folder.name
            resolved_names[folder_id] = name
        names.append(name)
    if state.root_id is not None and state.root_id not in ancestors:
        raise ValueError("Graph returned incomplete folder ancestry")
    return "/".join(names), resolved_names


async def select_documents(
    state: ScanState, snapshot: _DeltaSnapshot
) -> tuple[list[Document], set[str]]:
    """Select matching files and return documents with the updated deletion set.

    Args:
        state: Resolved selection rules and document mapper.
        snapshot: Completed delta contents to filter.

    Returns:
        Selected documents and deleted IDs, including previously visible items that
        no longer match the selection during an incremental scan.
    """
    selected: list[Document] = []
    deleted_ids = set(snapshot.deleted_ids)
    folder_names: dict[str, str] = {}
    for item in snapshot.matching_items.values():
        if item.name is None:
            continue
        extension = item.name.rpartition(".")[2].lower() if "." in item.name else ""
        if _selected_by_rules(state, item, extension, snapshot):
            if state.capture_paths:
                path, resolved_names = await _item_relative_path(
                    state, item, snapshot, folder_names
                )
                folder_names.update(resolved_names)
            else:
                path = None
            selected.append(state.document_mapper(state.drive_id, item, path))
        elif not snapshot.baseline:
            deleted_ids.add(item.id)
    return selected, deleted_ids
