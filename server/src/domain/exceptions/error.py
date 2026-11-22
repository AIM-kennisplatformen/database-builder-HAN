class DomainError(Exception):
    """Base class for domain errors."""

    failure_summary: dict[str, object] | None = None


class OperationFailure(DomainError):
    """An operation failure with a public-safe durable status and summary."""

    failure_code: str
    failure_summary: dict[str, object] | None

    def __init__(self, failure_code: str, failure_summary: dict[str, object]) -> None:
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.failure_summary = failure_summary
