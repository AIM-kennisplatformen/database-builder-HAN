import pytest

from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    MetadataSelection,
)


def test_metadata_selection_inherits_path_rules_and_applies_child_overrides() -> None:
    selection = MetadataSelection(
        include_rules=(
            MetadataPathRule("A", ("md", "pdf")),
            MetadataPathRule("A/B", ("docx",)),
        ),
        exclude_rules=(
            MetadataPathRule("A", ("xlsx",)),
            MetadataPathRule("A/B", ("pdf",)),
        ),
    )

    assert selection.includes("A/report", "md")
    assert selection.includes("A/report", "pdf")
    assert not selection.includes("A/report", "xlsx")
    assert selection.includes("A/B/report", "md")
    assert selection.includes("A/B/report", "docx")
    assert not selection.includes("A/B/report", "pdf")
    assert not selection.includes("A/B/report", "xlsx")


def test_metadata_selection_matches_paths_by_folder_boundary() -> None:
    selection = MetadataSelection(
        include_rules=(MetadataPathRule("Research", ("pdf",)),),
        exclude_rules=(),
    )

    assert selection.includes("Research/report", "pdf")
    assert not selection.includes("Research-old/report", "pdf")
    assert not selection.includes("Elsewhere/report", "pdf")


def test_metadata_selection_matches_folder_names_case_insensitively() -> None:
    selection = MetadataSelection(
        include_rules=(MetadataPathRule("Research/Reports", ("pdf",)),),
        exclude_rules=(),
    )

    assert selection.includes("research/REPORTS/report", "PDF")


def test_root_and_extensionless_selection() -> None:
    root_selection = MetadataSelection(
        include_rules=(MetadataPathRule("", ()),),
        exclude_rules=(),
    )
    assert root_selection.includes("report", "")
    assert root_selection.includes("folder/report", "pdf")

    extension_selection = MetadataSelection(
        include_rules=(MetadataPathRule("", ("pdf",)),),
        exclude_rules=(),
    )
    assert not extension_selection.includes("report", "")

    default_selection = MetadataSelection(include_rules=(), exclude_rules=())
    assert default_selection.includes("report", "")
    assert default_selection.includes("folder/report", "pdf")


def test_root_wildcard_include_survives_a_narrower_child_rule() -> None:
    selection = MetadataSelection(
        include_rules=(
            MetadataPathRule("", ()),
            MetadataPathRule("A", ("pdf",)),
        ),
        exclude_rules=(),
    )

    assert selection.includes("Elsewhere/report", "txt")
    assert selection.includes("A/report", "pdf")
    assert selection.includes("A/report", "txt")


def test_extension_matching_is_case_insensitive() -> None:
    selection = MetadataSelection(
        include_rules=(MetadataPathRule("A", ("pdf",)),),
        exclude_rules=(),
    )

    assert selection.includes("A/report", ".PDF")
    assert not selection.includes("A/report", "docx")


@pytest.mark.parametrize("path", ["Other", "A2/report", "Aardvark/report"])
def test_more_specific_rules_are_folder_scoped(path: str) -> None:
    selection = MetadataSelection(
        include_rules=(MetadataPathRule("A", ("pdf",)),),
        exclude_rules=(),
    )

    assert not selection.includes(path, "pdf")
