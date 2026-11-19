from collections.abc import Iterator
from typing import override

import pytest

from api.progress import ProgressEventLimiter
from api.stream import StreamEvent, StreamEventKind, StreamLifecycle


class SequenceLimiter(ProgressEventLimiter):
    def __init__(self, *decisions: bool) -> None:
        super().__init__(0)
        self.decisions: Iterator[bool] = iter(decisions)
        self.calls: int = 0

    @override
    def should_emit(self) -> bool:
        self.calls += 1
        return next(self.decisions)


def test_stream_lifecycle_limits_progress_and_rejects_second_terminal() -> None:
    limiter = SequenceLimiter(True, False)
    lifecycle = StreamLifecycle(
        limiter,
        StreamEvent("error", {"detail": "missing outcome"}, StreamEventKind.ERROR),
    )

    events = [
        lifecycle.encode(
            StreamEvent("progress", {"processed": 1}, StreamEventKind.PROGRESS)
        ),
        lifecycle.encode(
            StreamEvent("progress", {"processed": 2}, StreamEventKind.PROGRESS)
        ),
        lifecycle.encode(
            StreamEvent(
                "skipped", {"drive_id": "private"}, StreamEventKind.NON_TERMINAL
            )
        ),
        lifecycle.encode(
            StreamEvent("complete", {"processed": 2}, StreamEventKind.COMPLETE)
        ),
    ]

    assert [event.event for event in events if event is not None] == [
        "progress",
        "skipped",
        "complete",
    ]
    assert limiter.calls == 2
    with pytest.raises(RuntimeError, match="after its terminal event"):
        lifecycle.encode(
            StreamEvent("error", {"detail": "late"}, StreamEventKind.ERROR)
        )
    assert lifecycle.finish() is None


def test_stream_lifecycle_supplies_error_when_finished_without_outcome() -> None:
    lifecycle = StreamLifecycle(
        SequenceLimiter(),
        StreamEvent("error", {"detail": "missing outcome"}, StreamEventKind.ERROR),
    )

    assert (
        lifecycle.encode(
            StreamEvent(
                "skipped", {"drive_id": "private"}, StreamEventKind.NON_TERMINAL
            )
        )
        is not None
    )
    fallback = lifecycle.finish()

    assert fallback is not None
    assert fallback.event == "error"
    assert fallback.data == {"detail": "missing outcome"}
    assert lifecycle.finish() is None
