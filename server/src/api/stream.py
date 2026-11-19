"""Shared lifecycle mechanics for API server-sent event streams."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from fastapi.sse import ServerSentEvent

from api.progress import ProgressEventLimiter


class StreamEventKind(StrEnum):
    PROGRESS = "progress"
    NON_TERMINAL = "nonterminal"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass(frozen=True)
class StreamEvent:
    """An endpoint payload plus its role in the stream lifecycle."""

    name: str
    data: Mapping[str, object]
    kind: StreamEventKind

    def __post_init__(self) -> None:
        if self.kind is StreamEventKind.NON_TERMINAL:
            if self.name in (StreamEventKind.COMPLETE, StreamEventKind.ERROR):
                raise ValueError("nonterminal event cannot use a terminal name")
        elif self.name != self.kind.value:
            raise ValueError("event name must match its lifecycle kind")


class StreamLifecycle:
    """Rate-limit progress, serialize events, and enforce one terminal event."""

    def __init__(
        self,
        progress_limiter: ProgressEventLimiter,
        missing_outcome: StreamEvent,
    ) -> None:
        if missing_outcome.kind is not StreamEventKind.ERROR:
            raise ValueError("missing_outcome must be an error event")
        self._progress_limiter: ProgressEventLimiter = progress_limiter
        self._missing_outcome: StreamEvent = missing_outcome
        self._terminal_sent: bool = False

    def encode(self, event: StreamEvent) -> ServerSentEvent | None:
        if self._terminal_sent:
            raise RuntimeError("stream produced events after its terminal event")
        if event.kind is StreamEventKind.PROGRESS:
            if not self._progress_limiter.should_emit():
                return None
        elif event.kind in (StreamEventKind.COMPLETE, StreamEventKind.ERROR):
            self._terminal_sent = True

        return ServerSentEvent(event=event.name, data=dict(event.data))

    def finish(self) -> ServerSentEvent | None:
        """Emit the configured safe error if updates ended without an outcome."""
        if self._terminal_sent:
            return None
        self._terminal_sent = True
        return ServerSentEvent(
            event=self._missing_outcome.name,
            data=dict(self._missing_outcome.data),
        )
