"""Pure normalization and validation for group metadata selectors."""

from domain.exceptions.metadata_ingest_errors import InvalidMetadataSelection
from domain.models.documents.metadata_selection import (
    MetadataPathRule,
    normalize_metadata_rules,
)
from domain.use_cases.metadata_refresh.group._models import DriveMetadataSelector


def selector_rules(
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
        grouped.setdefault(selector.drive_id, []).extend(normalized_rules)
    return {
        drive_id: normalize_metadata_rules(tuple(rules), "selectors")
        for drive_id, rules in grouped.items()
    }


def validate_selector_conflicts(
    include_rules: dict[str, tuple[MetadataPathRule, ...]],
    exclude_rules: dict[str, tuple[MetadataPathRule, ...]],
) -> None:
    for drive_id in include_rules.keys() & exclude_rules.keys():
        for include_rule in include_rules[drive_id]:
            for exclude_rule in exclude_rules[drive_id]:
                if _metadata_rules_overlap(include_rule, exclude_rule):
                    raise InvalidMetadataSelection(
                        f"include and exclude selectors overlap for drive {drive_id} at {include_rule.path or '/'}"
                    )


def selected_drive_ids(
    group_drive_ids: tuple[str, ...],
    included_drive_ids: set[str],
    excluded_drive_ids: set[str],
    exclude_selectors: list[DriveMetadataSelector],
) -> tuple[str, ...]:
    whole_drive_excludes = {
        selector.drive_id
        for selector in exclude_selectors
        if not selector.paths and not selector.extensions
    }
    return tuple(
        drive_id
        for drive_id in group_drive_ids
        if (not included_drive_ids or drive_id in included_drive_ids)
        and (drive_id not in excluded_drive_ids or drive_id not in whole_drive_excludes)
    )


def _metadata_rules_overlap(
    include_rule: MetadataPathRule, exclude_rule: MetadataPathRule
) -> bool:
    if include_rule.path != exclude_rule.path:
        return False
    if not include_rule.extensions or not exclude_rule.extensions:
        return True
    return bool(set(include_rule.extensions) & set(exclude_rule.extensions))
