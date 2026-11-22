"""Collect and filter a Graph delta snapshot for one metadata selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, cast, final
from urllib.parse import quote

from httpx import Response
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_http.middleware.options import ResponseHandlerOption
from msgraph.graph_service_client import GraphServiceClient

from domain.exceptions.metadata_ingest_errors import MetadataSourceError
from domain.models.documents.document import Document
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    MetadataSelection,
)
from domain.ports.documents.document_metadata_source import DocumentMetadataScan
from infrastructure.msgraph.base import raise_for_graph_error, validate_graph_url
from infrastructure.msgraph.document_metadata.models import (
    GraphDeltaPage,
    GraphDriveItem,
)


class _MetadataScanSource(Protocol):
    client: GraphServiceClient

    async def folder_id(self, drive_id: str, path: str, root_item_id: str) -> str: ...

    def document(
        self, drive_id: str, item: GraphDriveItem, relative_path: str | None
    ) -> Document: ...


class _DeltaRequest(Protocol):
    def with_url(self, url: str) -> _DeltaRequest: ...

    async def get(
        self, request_configuration: RequestConfiguration[object]
    ) -> object | None: ...


@dataclass
class _DeltaSnapshot:
    matching_items: dict[str, GraphDriveItem] = field(default_factory=dict)
    folder_parents: dict[str, str | None] = field(default_factory=dict)
    folder_names: dict[str, str] = field(default_factory=dict)
    deleted_ids: set[str] = field(default_factory=set)
    delta_link: str | None = None
    baseline: bool = False

    def reset(self, baseline_url: str) -> str:
        self.baseline = True
        self.matching_items.clear()
        self.folder_parents.clear()
        self.folder_names.clear()
        self.deleted_ids.clear()
        return baseline_url


@final
class _DocumentMetadataScan:
    source: _MetadataScanSource
    drive_id: str
    selection: MetadataSelection
    root_item_id: str
    cursor: str | None
    path_scoped: bool
    capture_paths: bool
    root_id: str | None
    rule_resolve_folder_ids: dict[str, str]

    def __init__(
        self,
        source: _MetadataScanSource,
        drive_id: str,
        selection: MetadataSelection,
        root_item_id: str,
        cursor: str | None,
        capture_relative_paths: bool,
    ) -> None:
        self.source = source
        self.drive_id = drive_id
        self.selection = selection
        self.root_item_id = root_item_id
        self.cursor = cursor
        self.path_scoped = any(
            rule.path for rule in selection.include_rules + selection.exclude_rules
        )
        self.capture_paths = self.path_scoped or capture_relative_paths
        self.root_id = None
        self.rule_resolve_folder_ids = {}

    async def run(self) -> DocumentMetadataScan:
        await self._resolve_path_scope()
        base_url = (
            f"https://graph.microsoft.com/v1.0/drives/{quote(self.drive_id, safe='')}"
        )
        delta_path = self._delta_path()
        baseline_url = f"{base_url}/{delta_path}/delta"
        snapshot = await self._read_delta(baseline_url)
        documents = await self._select_documents(snapshot)
        if snapshot.delta_link is None:
            raise ValueError("Graph did not provide a completed delta cursor")
        validate_graph_url(snapshot.delta_link)
        return DocumentMetadataScan(
            documents=tuple(documents),
            deleted_item_ids=tuple(sorted(snapshot.deleted_ids)),
            delta_link=snapshot.delta_link,
            baseline=snapshot.baseline,
        )

    async def _resolve_path_scope(self) -> None:
        if self.capture_paths:
            self.root_id = (
                await self.source.folder_id(self.drive_id, "", self.root_item_id)
                if self.root_item_id == "root"
                else self.root_item_id
            )
        if self.path_scoped:
            paths = {
                rule.path
                for rule in self.selection.include_rules + self.selection.exclude_rules
                if rule.path
            }
            for path in paths:
                self.rule_resolve_folder_ids[path] = await self.source.folder_id(
                    self.drive_id, path, self.root_item_id
                )

    def _delta_path(self) -> str:
        if self.root_item_id == "root":
            return "root"
        return f"items/{quote(self.root_item_id, safe='')}"

    def _delta_builder(self) -> _DeltaRequest:
        return cast(
            _DeltaRequest,
            cast(
                object,
                self.source.client.drives.by_drive_id(self.drive_id)
                .items.by_drive_item_id(self.root_item_id)
                .delta,
            ),
        )

    async def _read_delta(self, baseline_url: str) -> _DeltaSnapshot:
        baseline = self.cursor is None or self.path_scoped or self.capture_paths
        snapshot = _DeltaSnapshot(baseline=baseline)
        next_url = baseline_url if baseline else self.cursor
        seen_urls: set[str] = set()
        delta = self._delta_builder()
        while next_url is not None:
            next_url = self._validate_next_url(next_url, seen_urls)
            response = await self._get_delta_response(delta, next_url)
            page = self._parse_delta_response(response, snapshot, baseline_url)
            if page is None:
                next_url = baseline_url
                seen_urls.clear()
                continue
            self._apply_page(snapshot, page)
            next_url = page.next_link
            if next_url is None:
                if page.delta_link is None:
                    raise ValueError("Graph did not complete the drive delta scan")
                snapshot.delta_link = page.delta_link
        return snapshot

    @staticmethod
    def _validate_next_url(next_url: str, seen_urls: set[str]) -> str:
        if next_url in seen_urls:
            raise ValueError("Graph repeated an @odata.nextLink")
        seen_urls.add(next_url)
        validate_graph_url(next_url)
        return next_url

    async def _get_delta_response(
        self, delta: _DeltaRequest, next_url: str
    ) -> Response:
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
        return response

    def _parse_delta_response(
        self,
        response: Response,
        snapshot: _DeltaSnapshot,
        baseline_url: str,
    ) -> GraphDeltaPage | None:
        try:
            raise_for_graph_error(response, "Graph rejected the drive metadata request")
        except MetadataSourceError as exc:
            if exc.status_code != 410 or snapshot.baseline:
                raise
            _ = snapshot.reset(baseline_url)
            return None
        return GraphDeltaPage.model_validate_json(response.content)

    @staticmethod
    def _apply_page(snapshot: _DeltaSnapshot, page: GraphDeltaPage) -> None:
        for item in page.value:
            _DocumentMetadataScan._apply_item(snapshot, item)

    @staticmethod
    def _apply_item(snapshot: _DeltaSnapshot, item: GraphDriveItem) -> None:
        _DocumentMetadataScan._apply_folder(item, snapshot)
        excluded = item.deleted is not None or item.file is None or not item.name
        _DocumentMetadataScan._apply_visibility(item, snapshot, excluded)

    @staticmethod
    def _apply_folder(item: GraphDriveItem, snapshot: _DeltaSnapshot) -> None:
        if item.deleted is not None or item.folder is None:
            _ = snapshot.folder_parents.pop(item.id, None)
            return
        snapshot.folder_parents[item.id] = (
            item.parent_reference.id if item.parent_reference else None
        )
        if item.name:
            snapshot.folder_names[item.id] = item.name

    @staticmethod
    def _apply_visibility(
        item: GraphDriveItem, snapshot: _DeltaSnapshot, excluded: bool
    ) -> None:
        if item.deleted is not None or (excluded and not snapshot.baseline):
            snapshot.deleted_ids.add(item.id)
        else:
            snapshot.deleted_ids.discard(item.id)
        if excluded:
            _ = snapshot.matching_items.pop(item.id, None)
            return
        snapshot.matching_items[item.id] = item

    def _ancestor_ids(self, item: GraphDriveItem, snapshot: _DeltaSnapshot) -> set[str]:
        parent_id = item.parent_reference.id if item.parent_reference else None
        found: set[str] = set()
        seen: set[str] = set()
        while parent_id is not None:
            if parent_id in seen:
                raise ValueError("Graph returned incomplete folder ancestry")
            seen.add(parent_id)
            found.add(parent_id)
            if parent_id == self.root_id:
                break
            if parent_id not in snapshot.folder_parents:
                raise ValueError("Graph returned incomplete folder ancestry")
            parent_id = snapshot.folder_parents[parent_id]
        if parent_id is None or (
            self.root_id is not None and self.root_id not in found
        ):
            raise ValueError("Graph returned incomplete folder ancestry")
        return found

    def _selected_by_rules(
        self, item: GraphDriveItem, extension: str, snapshot: _DeltaSnapshot
    ) -> bool:
        ancestors: set[str] = (
            self._ancestor_ids(item, snapshot) if self.path_scoped else set()
        )
        candidates: list[tuple[int, bool]] = []
        for rules, included in (
            (self.selection.include_rules, True),
            (self.selection.exclude_rules, False),
        ):
            self._add_matching_rules(rules, included, extension, ancestors, candidates)
        if not candidates:
            return not self.selection.include_rules
        specificity = max(depth for depth, _ in candidates)
        return next(included for depth, included in candidates if depth == specificity)

    def _add_matching_rules(
        self,
        rules: tuple[MetadataPathRule, ...],
        included: bool,
        extension: str,
        ancestors: set[str],
        candidates: list[tuple[int, bool]],
    ) -> None:
        for rule in rules:
            rule_id = self.rule_resolve_folder_ids.get(rule.path, self.root_id)
            if rule_id is not None and rule_id not in ancestors:
                continue
            if rule.extensions and extension not in rule.extensions:
                continue
            candidates.append((rule.path.count("/") + bool(rule.path), included))

    async def _item_relative_path(
        self, item: GraphDriveItem, snapshot: _DeltaSnapshot
    ) -> str:
        ancestors = self._ancestor_ids(item, snapshot)
        parent_id = item.parent_reference.id if item.parent_reference else None
        ordered: list[str] = []
        while parent_id is not None and parent_id != self.root_id:
            ordered.append(parent_id)
            parent_id = snapshot.folder_parents.get(parent_id)
        ordered.reverse()
        names: list[str] = []
        for folder_id in ordered:
            names.append(await self._folder_name(folder_id, snapshot))
        if self.root_id is not None and self.root_id not in ancestors:
            raise ValueError("Graph returned incomplete folder ancestry")
        return "/".join(names)

    async def _folder_name(self, folder_id: str, snapshot: _DeltaSnapshot) -> str:
        name = snapshot.folder_names.get(folder_id)
        if name is not None:
            return name
        response = cast(
            Response | None,
            await self.source.client.drives.by_drive_id(self.drive_id)
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
        snapshot.folder_names[folder_id] = folder.name
        return folder.name

    async def _select_documents(self, snapshot: _DeltaSnapshot) -> list[Document]:
        selected: list[Document] = []
        for item in snapshot.matching_items.values():
            if item.name is None:
                continue
            extension = item.name.rpartition(".")[2].lower() if "." in item.name else ""
            if self._selected_by_rules(item, extension, snapshot):
                path = (
                    await self._item_relative_path(item, snapshot)
                    if self.capture_paths
                    else None
                )
                selected.append(self.source.document(self.drive_id, item, path))
            elif not snapshot.baseline:
                snapshot.deleted_ids.add(item.id)
        return selected


async def scan_document_metadata(
    source: _MetadataScanSource,
    drive_id: str,
    selection: MetadataSelection,
    root_item_id: str,
    cursor: str | None,
    capture_relative_paths: bool,
) -> DocumentMetadataScan:
    return await _DocumentMetadataScan(
        source, drive_id, selection, root_item_id, cursor, capture_relative_paths
    ).run()
