"""Transport-neutral API error data and failure logging."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublicError:
    code: str
    http_status: int
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "http_status": self.http_status,
            "detail": self.detail,
        }


def build_internal_error() -> PublicError:
    """Return the generic safe description for an unexpected failure."""
    return PublicError("internal_error", 500, "The operation failed")


def log_api_failure(
    operation: str,
    exc: Exception,
    public_error: PublicError,
    *,
    identifiers: Mapping[str, object],
) -> None:
    """Log failures once, retaining tracebacks only for unexpected/server errors."""
    context = {key: value for key, value in identifiers.items() if value is not None}
    if public_error.code == "internal_error":
        logger.exception(
            "Unexpected API operation failure operation=%s identifiers=%s",
            operation,
            context,
        )
    elif public_error.http_status >= 500:
        cause = exc.__cause__
        if cause is not None:
            logger.error(
                "API operation failed operation=%s code=%s identifiers=%s",
                operation,
                public_error.code,
                context,
                exc_info=(type(cause), cause, cause.__traceback__),
            )
        else:
            logger.error(
                "API operation failed operation=%s code=%s identifiers=%s",
                operation,
                public_error.code,
                context,
            )


def log_missing_outcome(operation: str, *, identifiers: Mapping[str, object]) -> None:
    """Log when a stream ends without an outcome, without inventing a traceback."""
    context = {key: value for key, value in identifiers.items() if value is not None}
    logger.error(
        "API stream ended without an outcome operation=%s identifiers=%s",
        operation,
        context,
    )
