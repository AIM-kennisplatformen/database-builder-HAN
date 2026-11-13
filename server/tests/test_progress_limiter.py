"""Verify progress events respect the configured interval."""

import pytest

from api import progress


def test_progress_limiter_emits_again_at_interval_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((10.0, 10.2, 10.25))
    monkeypatch.setattr(progress, "monotonic", lambda: next(times))
    limiter = progress.ProgressEventLimiter(0.25)

    assert limiter.should_emit()
    assert not limiter.should_emit()
    assert limiter.should_emit()


def test_zero_interval_emits_every_progress_event() -> None:
    limiter = progress.ProgressEventLimiter(0)

    assert limiter.should_emit()
    assert limiter.should_emit()
