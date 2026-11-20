class DomainError(Exception):
    """Base class for domain errors."""

    failure_summary: dict[str, object] | None = None
