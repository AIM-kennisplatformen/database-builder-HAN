"""Control the frequency of progress events for one SSE response."""

from time import monotonic


class ProgressEventLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds: float = interval_seconds
        self.last_emitted_at: float | None = None

    def should_emit(self) -> bool:
        now = monotonic()
        if (
            self.last_emitted_at is not None
            and now - self.last_emitted_at < self.interval_seconds
        ):
            return False

        self.last_emitted_at = now
        return True
