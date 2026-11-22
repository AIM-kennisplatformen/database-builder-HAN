"""Document processing state and ingestion-only file selection rules."""

from dataclasses import dataclass
from enum import StrEnum

from domain.exceptions.metadata_ingest_errors import InvalidMetadataSelection
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    normalize_metadata_rules,
)


class ProcessingStatus(StrEnum):
    NEW = "new"
    DEFERRED = "deferred"
    OUTDATED = "outdated"
    FAILED = "failed"
    UNCHANGED = "unchanged"
    NO_USABLE_CHUNKS = "no_usable_chunks"


class ProcessingOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NO_USABLE_CHUNKS = "no_usable_chunks"


DEFAULT_INCLUDE_STATUSES = frozenset(
    {ProcessingStatus.NEW, ProcessingStatus.DEFERRED, ProcessingStatus.OUTDATED}
)


@dataclass(frozen=True)
class DocumentProcessingState:
    active_content_version: str | None = None
    active_policy_version: str | None = None
    last_attempt_content_version: str | None = None
    last_attempt_policy_version: str | None = None
    last_outcome: ProcessingOutcome | None = None
    deferred: bool = False


@dataclass(frozen=True)
class DocumentProcessingCandidate:
    drive_id: str
    item_id: str
    name: str
    relative_path: str | None
    content_version: str | None
    state: DocumentProcessingState | None


@dataclass(frozen=True)
class DocumentIngestFileSelection:
    drive_id: str
    item_id: str
    relative_path: str
    source_content_version: str | None
    processing_policy_version: str
    statuses: tuple[ProcessingStatus, ...]
    selection_reasons: tuple[str, ...]
    outcome: str = "selected"


@dataclass(frozen=True)
class IngestionDriveSelector:
    drive_id: str
    paths: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    statuses: tuple[ProcessingStatus, ...] | None = None


@dataclass(frozen=True)
class DocumentIngestRequest:
    group_id: str
    processing_policy_version: str
    include: tuple[IngestionDriveSelector, ...] = ()
    exclude: tuple[IngestionDriveSelector, ...] = ()

    def __post_init__(self) -> None:
        if not self.group_id.strip():
            raise InvalidMetadataSelection("group_id is required")
        if not self.processing_policy_version.strip():
            raise InvalidMetadataSelection("processing_policy_version is required")
        include = _normalize_selectors(self.include, "include")
        exclude = _normalize_selectors(self.exclude, "exclude")
        _validate_status_conflicts(include, exclude)
        object.__setattr__(self, "include", include)
        object.__setattr__(self, "exclude", exclude)


def _normalize_selectors(
    selectors: tuple[IngestionDriveSelector, ...], field_name: str
) -> tuple[IngestionDriveSelector, ...]:
    normalized: set[IngestionDriveSelector] = set()
    for selector in selectors:
        if not selector.drive_id.strip():
            raise InvalidMetadataSelection("drive_id is required")
        if selector.paths and any(
            not path.replace("\\", "/").strip("/") for path in selector.paths
        ):
            raise InvalidMetadataSelection(
                "paths must contain folder paths relative to the scan root"
            )
        path_rules = normalize_metadata_rules(
            tuple(
                MetadataPathRule(path, selector.extensions)
                for path in (selector.paths or ("",))
            ),
            field_name,
        )
        try:
            statuses = (
                None
                if selector.statuses is None
                else tuple(
                    sorted({ProcessingStatus(status) for status in selector.statuses})
                )
            )
        except ValueError as exc:
            raise InvalidMetadataSelection("invalid ingestion status") from exc
        normalized.update(
            IngestionDriveSelector(
                selector.drive_id,
                (rule.path,),
                rule.extensions,
                statuses,
            )
            for rule in path_rules
        )
    return tuple(
        sorted(
            normalized,
            key=lambda selector: (
                selector.drive_id,
                selector.paths,
                selector.extensions,
                tuple(selector.statuses or ()),
            ),
        )
    )


def _statuses_can_coexist(left: ProcessingStatus, right: ProcessingStatus) -> bool:
    if left == right:
        return True
    pair = frozenset({left, right})
    if ProcessingStatus.NEW in pair:
        return pair in (
            frozenset({ProcessingStatus.NEW, ProcessingStatus.DEFERRED}),
            frozenset({ProcessingStatus.NEW, ProcessingStatus.OUTDATED}),
        )
    return pair not in (
        frozenset({ProcessingStatus.OUTDATED, ProcessingStatus.UNCHANGED}),
        frozenset({ProcessingStatus.FAILED, ProcessingStatus.NO_USABLE_CHUNKS}),
        frozenset({ProcessingStatus.NEW, ProcessingStatus.NO_USABLE_CHUNKS}),
        frozenset({ProcessingStatus.UNCHANGED, ProcessingStatus.NO_USABLE_CHUNKS}),
    )


def _validate_status_conflicts(
    include: tuple[IngestionDriveSelector, ...],
    exclude: tuple[IngestionDriveSelector, ...],
) -> None:
    by_drive_include: dict[str, list[IngestionDriveSelector]] = {}
    by_drive_exclude: dict[str, list[IngestionDriveSelector]] = {}
    for selector in include:
        by_drive_include.setdefault(selector.drive_id, []).append(selector)
    for selector in exclude:
        by_drive_exclude.setdefault(selector.drive_id, []).append(selector)

    for drive_id in by_drive_include.keys() & by_drive_exclude.keys():
        for included in by_drive_include[drive_id]:
            for excluded in by_drive_exclude[drive_id]:
                if _status_selectors_conflict(included, excluded):
                    raise InvalidMetadataSelection(
                        f"include and exclude ingestion selectors overlap for drive {drive_id} at {included.paths[0] or '/'}"
                    )


def _status_selectors_conflict(
    included: IngestionDriveSelector, excluded: IngestionDriveSelector
) -> bool:
    if included.paths != excluded.paths:
        return False
    if (
        included.extensions
        and excluded.extensions
        and not set(included.extensions) & set(excluded.extensions)
    ):
        return False
    include_statuses = (
        included.statuses if included.statuses is not None else DEFAULT_INCLUDE_STATUSES
    )
    exclude_statuses = (
        excluded.statuses if excluded.statuses is not None else tuple(ProcessingStatus)
    )
    return any(
        _statuses_can_coexist(include_status, exclude_status)
        for include_status in include_statuses
        for exclude_status in exclude_statuses
    )


def derive_processing_statuses(
    candidate: DocumentProcessingCandidate, processing_policy_version: str
) -> frozenset[ProcessingStatus]:
    """Derive independent processing tags from current source and policy state."""
    state = candidate.state
    if state is None:
        return frozenset({ProcessingStatus.NEW})

    statuses: set[ProcessingStatus] = set()
    if state.deferred:
        statuses.add(ProcessingStatus.DEFERRED)
    if state.last_outcome is ProcessingOutcome.FAILED:
        statuses.add(ProcessingStatus.FAILED)
    elif state.last_outcome is ProcessingOutcome.NO_USABLE_CHUNKS:
        statuses.add(ProcessingStatus.NO_USABLE_CHUNKS)

    if state.active_content_version is None and state.last_outcome is None:
        statuses.add(ProcessingStatus.NEW)

    if state.active_content_version is not None:
        if (
            state.last_outcome is not ProcessingOutcome.NO_USABLE_CHUNKS
            and candidate.content_version is not None
            and candidate.content_version == state.active_content_version
            and state.active_policy_version == processing_policy_version
        ):
            statuses.add(ProcessingStatus.UNCHANGED)
        else:
            statuses.add(ProcessingStatus.OUTDATED)
    elif (
        state.last_attempt_content_version is not None or state.last_outcome is not None
    ) and (
        candidate.content_version is None
        or state.last_attempt_content_version is None
        or candidate.content_version != state.last_attempt_content_version
        or processing_policy_version != state.last_attempt_policy_version
    ):
        statuses.add(ProcessingStatus.OUTDATED)

    return frozenset(statuses)


def _matches_path(path: str | None, selector: IngestionDriveSelector) -> bool:
    if not selector.paths or any(not selected_path for selected_path in selector.paths):
        return True
    if path is None:
        return False
    candidate_path = path.casefold().strip("/")
    return any(
        candidate_path == selected_path
        or candidate_path.startswith(f"{selected_path}/")
        for selected_path in selector.paths
    )


def _matches_extension(name: str, selector: IngestionDriveSelector) -> bool:
    if not selector.extensions:
        return True
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    return extension in selector.extensions


def _effective_statuses(
    selector: IngestionDriveSelector,
    side: tuple[IngestionDriveSelector, ...],
    candidate: DocumentProcessingCandidate,
    *,
    include: bool,
) -> frozenset[ProcessingStatus]:
    if selector.statuses is not None:
        return frozenset(selector.statuses)
    ancestors = [
        rule
        for rule in side
        if rule.drive_id == selector.drive_id
        and rule.statuses is not None
        and _matches_path(candidate.relative_path, rule)
        and _matches_extension(candidate.name, rule)
        and _matches_path(candidate.relative_path, selector)
        and _matches_extension(candidate.name, selector)
        and (len(rule.paths[0].split("/")) if rule.paths[0] else 0)
        <= (len(selector.paths[0].split("/")) if selector.paths[0] else 0)
    ]
    if not ancestors:
        return DEFAULT_INCLUDE_STATUSES if include else frozenset(ProcessingStatus)
    depth = max(
        len(rule.paths[0].split("/")) if rule.paths[0] else 0 for rule in ancestors
    )
    return frozenset(
        status
        for rule in ancestors
        if (len(rule.paths[0].split("/")) if rule.paths[0] else 0) == depth
        for status in (rule.statuses or ())
    )


def select_ingest_file(
    candidate: DocumentProcessingCandidate, request: DocumentIngestRequest
) -> DocumentIngestFileSelection | None:
    """Select a candidate by path, extension, and its current processing tags."""
    if not _candidate_drive_is_included(candidate, request):
        return None
    if _needs_candidate_path(candidate, request) and candidate.relative_path is None:
        raise InvalidMetadataSelection(
            "candidate folder path is unavailable for path-scoped ingestion"
        )
    statuses = derive_processing_statuses(candidate, request.processing_policy_version)
    include = tuple(
        selector
        for selector in request.include
        if selector.drive_id == candidate.drive_id
    )
    exclude = tuple(
        selector
        for selector in request.exclude
        if selector.drive_id == candidate.drive_id
    )
    default_include = not include
    matching = _matching_ingest_rules(candidate, statuses, include, exclude)
    if default_include:
        matching.extend(_default_include_rule(statuses))
    reasons = _included_rule_reasons(matching)
    if reasons is None:
        return None
    if candidate.content_version is None:
        reasons += ("source_content_version_unknown",)
    return DocumentIngestFileSelection(
        candidate.drive_id,
        candidate.item_id,
        candidate.relative_path or "",
        candidate.content_version,
        request.processing_policy_version,
        tuple(sorted(statuses)),
        reasons,
    )


def _candidate_drive_is_included(
    candidate: DocumentProcessingCandidate, request: DocumentIngestRequest
) -> bool:
    return not request.include or any(
        selector.drive_id == candidate.drive_id for selector in request.include
    )


def _needs_candidate_path(
    candidate: DocumentProcessingCandidate, request: DocumentIngestRequest
) -> bool:
    return any(
        selector.drive_id == candidate.drive_id and any(path for path in selector.paths)
        for selector in request.include + request.exclude
    )


def _matching_ingest_rules(
    candidate: DocumentProcessingCandidate,
    statuses: frozenset[ProcessingStatus],
    include: tuple[IngestionDriveSelector, ...],
    exclude: tuple[IngestionDriveSelector, ...],
) -> list[tuple[int, bool, str]]:
    matches: list[tuple[int, bool, str]] = []
    directives = tuple((rule, True, include) for rule in include) + tuple(
        (rule, False, exclude) for rule in exclude
    )
    for rule, included, side in directives:
        if _matches_path(candidate.relative_path, rule) and _matches_extension(
            candidate.name, rule
        ):
            allowed = _effective_statuses(rule, side, candidate, include=included)
            if statuses & allowed:
                matches.append(
                    (
                        _path_depth(rule),
                        included,
                        _selection_reason(rule, statuses & allowed),
                    )
                )
    return matches


def _default_include_rule(
    statuses: frozenset[ProcessingStatus],
) -> list[tuple[int, bool, str]]:
    included_statuses = statuses & DEFAULT_INCLUDE_STATUSES
    if not included_statuses:
        return []
    return [
        (
            0,
            True,
            f"default:{','.join(sorted(status.value for status in included_statuses))}",
        )
    ]


def _included_rule_reasons(
    matches: list[tuple[int, bool, str]],
) -> tuple[str, ...] | None:
    if not matches:
        return None
    specificity = max(depth for depth, _, _ in matches)
    most_specific = [entry for entry in matches if entry[0] == specificity]
    if not any(included for _, included, _ in most_specific):
        return None
    if any(not included for _, included, _ in most_specific):
        return None
    return tuple(sorted({reason for _, _, reason in most_specific}))


def _path_depth(selector: IngestionDriveSelector) -> int:
    return len(selector.paths[0].split("/")) if selector.paths[0] else 0


def _selection_reason(
    selector: IngestionDriveSelector, statuses: frozenset[ProcessingStatus]
) -> str:
    return (
        f"{selector.paths[0] or '/'};extensions={','.join(selector.extensions) or '*'};"
        f"statuses={','.join(sorted(status.value for status in statuses))}"
    )
