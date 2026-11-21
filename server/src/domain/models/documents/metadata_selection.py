"""The normalized path and extension rules that identify a catalog view."""

from dataclasses import dataclass

from domain.exceptions.metadata_ingest_errors import InvalidMetadataSelection


@dataclass(frozen=True)
class MetadataPathRule:
    """An extension filter applying at a folder path (empty means the root)."""

    path: str
    extensions: tuple[str, ...]


def normalize_metadata_rules(
    rules: tuple[MetadataPathRule, ...], field_name: str
) -> tuple[MetadataPathRule, ...]:
    """Validate and canonicalize path and extension selectors."""
    normalized_rules: set[MetadataPathRule] = set()
    for rule in rules:
        raw_path = rule.path.replace("\\", "/")
        path = raw_path.strip("/")
        if raw_path and not path:
            raise InvalidMetadataSelection(
                f"{field_name} must contain folder paths relative to the scan root"
            )
        if path and any(
            not segment.strip() or segment in {".", ".."} for segment in path.split("/")
        ):
            raise InvalidMetadataSelection(
                f"{field_name} must contain folder paths relative to the scan root"
            )
        extensions = tuple(
            sorted(
                {
                    extension.strip().lower().removeprefix(".")
                    for extension in rule.extensions
                }
            )
        )
        if any(
            not extension.isascii() or not extension.isalnum()
            for extension in extensions
        ):
            raise InvalidMetadataSelection("extensions must be alphanumeric")
        normalized_rules.add(MetadataPathRule(path.casefold(), extensions))
    return tuple(
        sorted(normalized_rules, key=lambda rule: (rule.path, rule.extensions))
    )


@dataclass(frozen=True)
class MetadataSelection:
    include_rules: tuple[MetadataPathRule, ...]
    exclude_rules: tuple[MetadataPathRule, ...]

    def __post_init__(self) -> None:
        include_rules = normalize_metadata_rules(self.include_rules, "include_rules")
        exclude_rules = normalize_metadata_rules(self.exclude_rules, "exclude_rules")
        if include_rules == (MetadataPathRule("", ()),):
            include_rules = ()
        object.__setattr__(self, "include_rules", include_rules)
        object.__setattr__(self, "exclude_rules", exclude_rules)

    def includes(self, path: str, extension: str) -> bool:
        """Resolve the most-specific applicable include or exclude rule."""
        path = path.replace("\\", "/").strip("/").casefold()
        candidates: list[tuple[int, bool]] = []
        for rules, included in (
            (self.include_rules, True),
            (self.exclude_rules, False),
        ):
            for rule in rules:
                if (
                    rule.path
                    and path != rule.path
                    and not path.startswith(f"{rule.path}/")
                ):
                    continue
                if (
                    rule.extensions
                    and extension.lower().removeprefix(".") not in rule.extensions
                ):
                    continue
                candidates.append((rule.path.count("/") + bool(rule.path), included))

        if not candidates:
            return not self.include_rules
        specificity = max(depth for depth, _ in candidates)
        return next(included for depth, included in candidates if depth == specificity)
