from typing import cast

import pytest

from domain.exceptions.metadata_ingest_errors import InvalidMetadataSelection
from domain.models.documents.document_ingestion import (
    DocumentIngestRequest,
    DocumentProcessingCandidate,
    DocumentProcessingState,
    IngestionDriveSelector,
    ProcessingOutcome,
    ProcessingStatus,
    derive_processing_statuses,
    select_ingest_file,
)


def _request(
    *,
    include: tuple[IngestionDriveSelector, ...] = (),
    exclude: tuple[IngestionDriveSelector, ...] = (),
) -> DocumentIngestRequest:
    return DocumentIngestRequest(
        group_id="group",
        processing_policy_version="policy-v1",
        include=include,
        exclude=exclude,
    )


def _candidate(
    state: DocumentProcessingState | None,
    *,
    content_version: str | None = "v2",
    relative_path: str | None = "Folder/report.pdf",
) -> DocumentProcessingCandidate:
    return DocumentProcessingCandidate(
        drive_id="drive",
        item_id="item",
        name=(relative_path or "Report.pdf").rpartition("/")[2],
        relative_path=relative_path,
        content_version=content_version,
        state=state,
    )


def test_processing_statuses_are_coexisting_tags() -> None:
    new_deferred = _candidate(
        DocumentProcessingState(is_deferred=True),
    )
    outdated_failed = _candidate(
        DocumentProcessingState(
            active_content_version="v1",
            active_policy_version="policy-v1",
            last_outcome=ProcessingOutcome.FAILED,
        ),
    )

    assert derive_processing_statuses(new_deferred, "policy-v1") == {
        ProcessingStatus.NEW,
        ProcessingStatus.DEFERRED,
    }
    assert derive_processing_statuses(outdated_failed, "policy-v1") == {
        ProcessingStatus.OUTDATED,
        ProcessingStatus.FAILED,
    }


def test_prior_outcome_without_versions_is_not_silently_unselectable() -> None:
    candidate = _candidate(
        DocumentProcessingState(last_outcome=ProcessingOutcome.FAILED),
        content_version=None,
    )

    statuses = derive_processing_statuses(candidate, "policy-v1")

    assert ProcessingStatus.UNCHANGED not in statuses
    assert statuses
    assert select_ingest_file(candidate, _request()) is not None


def test_no_usable_chunks_with_prior_active_success_is_outdated_not_unchanged() -> None:
    candidate = _candidate(
        DocumentProcessingState(
            active_content_version="v2",
            active_policy_version="policy-v1",
            last_attempt_content_version="v2",
            last_attempt_policy_version="policy-v1",
            last_outcome=ProcessingOutcome.NO_USABLE_CHUNKS,
        )
    )

    statuses = derive_processing_statuses(candidate, "policy-v1")

    assert ProcessingStatus.OUTDATED in statuses
    assert ProcessingStatus.NO_USABLE_CHUNKS in statuses
    assert ProcessingStatus.UNCHANGED not in statuses


def test_missing_content_version_after_active_success_is_outdated() -> None:
    candidate = _candidate(
        DocumentProcessingState(
            active_content_version="v1", active_policy_version="policy-v1"
        ),
        content_version=None,
    )

    assert derive_processing_statuses(candidate, "policy-v1") == {
        ProcessingStatus.OUTDATED
    }


def test_discarded_first_attempt_can_be_new_and_outdated_and_conflicts() -> None:
    candidate = _candidate(
        DocumentProcessingState(
            last_attempt_content_version="v1",
            last_attempt_policy_version="policy-v1",
        ),
    )

    assert derive_processing_statuses(candidate, "policy-v1") == {
        ProcessingStatus.NEW,
        ProcessingStatus.OUTDATED,
    }
    with pytest.raises(InvalidMetadataSelection):
        _request(
            include=(IngestionDriveSelector(drive_id="drive"),),
            exclude=(
                IngestionDriveSelector(
                    drive_id="drive", statuses=(ProcessingStatus.OUTDATED,)
                ),
            ),
        )


def test_empty_default_include_allows_root_failed_exclusion() -> None:
    request = _request(
        exclude=(
            IngestionDriveSelector(
                drive_id="drive", statuses=(ProcessingStatus.FAILED,)
            ),
        )
    )
    new_file = _candidate(None)
    failed_file = _candidate(
        DocumentProcessingState(
            last_attempt_content_version="v2",
            last_attempt_policy_version="policy-v1",
            last_outcome=ProcessingOutcome.FAILED,
        )
    )

    assert select_ingest_file(new_file, request) is not None
    assert select_ingest_file(failed_file, request) is None


def test_omitted_drive_paths_select_candidates_nested_below_root() -> None:
    request = _request(
        include=(IngestionDriveSelector(drive_id="drive"),),
    )

    selected = select_ingest_file(_candidate(None), request)

    assert selected is not None
    assert selected.item_id == "item"


def test_root_extension_rule_matches_candidate_without_relative_path() -> None:
    request = _request(
        include=(IngestionDriveSelector(drive_id="drive", extensions=("pdf",)),),
    )
    candidate = _candidate(None, relative_path=None)

    selected = select_ingest_file(candidate, request)

    assert selected is not None


def test_explicit_root_include_and_equal_path_failed_exclude_conflict() -> None:
    with pytest.raises(InvalidMetadataSelection):
        _request(
            include=(IngestionDriveSelector(drive_id="drive"),),
            exclude=(
                IngestionDriveSelector(
                    drive_id="drive", statuses=(ProcessingStatus.FAILED,)
                ),
            ),
        )


def test_equal_path_impossible_status_overlap_is_accepted() -> None:
    request = _request(
        include=(
            IngestionDriveSelector(
                drive_id="drive",
                paths=("Folder",),
                statuses=(ProcessingStatus.UNCHANGED,),
            ),
        ),
        exclude=(
            IngestionDriveSelector(
                drive_id="drive",
                paths=("Folder",),
                statuses=(ProcessingStatus.OUTDATED,),
            ),
        ),
    )
    candidate = _candidate(
        DocumentProcessingState(
            active_content_version="v2", active_policy_version="policy-v1"
        )
    )

    assert select_ingest_file(candidate, request) is not None


def test_ingestion_selection_uses_path_precedence_and_casefolded_boundaries() -> None:
    request = _request(
        include=(
            IngestionDriveSelector(
                drive_id="drive", paths=("Library",), extensions=("pdf",)
            ),
            IngestionDriveSelector(
                drive_id="drive",
                paths=("Library/Reports",),
                extensions=("pdf",),
                statuses=(ProcessingStatus.OUTDATED,),
            ),
        ),
    )
    candidate = _candidate(
        DocumentProcessingState(
            active_content_version="v1", active_policy_version="policy-v1"
        ),
        relative_path="library/reports/report.pdf",
    )

    selected = select_ingest_file(candidate, request)

    assert selected is not None
    assert selected.statuses == (ProcessingStatus.OUTDATED,)
    assert selected.selection_reasons == (
        "library/reports;extensions=pdf;statuses=outdated",
    )
    assert (
        select_ingest_file(
            _candidate(candidate.state, relative_path="Library-old/report.pdf"), request
        )
        is None
    )


def test_extensionless_files_match_only_wildcard_extension_selector() -> None:
    candidate = _candidate(None, relative_path="Folder/README")
    wildcard = _request(
        include=(IngestionDriveSelector(drive_id="drive", paths=("Folder",)),)
    )
    pdf_only = _request(
        include=(
            IngestionDriveSelector(
                drive_id="drive", paths=("Folder",), extensions=("pdf",)
            ),
        )
    )

    assert select_ingest_file(candidate, wildcard) is not None
    assert select_ingest_file(candidate, pdf_only) is None


def test_empty_status_selector_matches_no_statuses() -> None:
    request = _request(
        include=(IngestionDriveSelector(drive_id="drive", statuses=()),),
    )

    assert select_ingest_file(_candidate(None), request) is None


def test_invalid_status_value_is_rejected() -> None:
    with pytest.raises(InvalidMetadataSelection):
        _request(
            include=(
                IngestionDriveSelector(
                    drive_id="drive",
                    statuses=cast(tuple[ProcessingStatus, ...], ("not-a-status",)),
                ),
            ),
        )


def test_child_path_without_statuses_inherits_matching_parent_status_rule() -> None:
    request = _request(
        include=(
            IngestionDriveSelector(
                drive_id="drive",
                paths=("Folder",),
                statuses=(ProcessingStatus.OUTDATED,),
            ),
            IngestionDriveSelector(drive_id="drive", paths=("Folder/Reports",)),
        ),
    )
    outdated = _candidate(
        DocumentProcessingState(active_content_version="v1"),
        relative_path="Folder/Reports/report.pdf",
    )
    failed = _candidate(
        DocumentProcessingState(
            last_attempt_content_version="v2",
            last_attempt_policy_version="policy-v1",
            last_outcome=ProcessingOutcome.FAILED,
        ),
        relative_path="Folder/Reports/report.pdf",
    )

    assert select_ingest_file(outdated, request) is not None
    assert select_ingest_file(failed, request) is None
