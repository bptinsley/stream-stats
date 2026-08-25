from __future__ import annotations

import random

import pytest

from stream_stats import WindowStatistics
from stream_stats._go_window_statistics import go_library_available


pytestmark = pytest.mark.skipif(
    not go_library_available(), reason="Go shared library is not built"
)


def test_go_bulk_lifecycle_and_random_updates() -> None:
    rng = random.Random(991)
    reference = WindowStatistics(29)
    with WindowStatistics(29, backend="go") as go:
        initial = [float(index) for index in range(35)]
        assert go.add_many(initial) == reference.add_many(initial)
        for _ in range(1500):
            value = float(rng.randrange(-30, 31))
            assert go.add(value) == reference.add(value)
            if rng.random() < 0.06 and reference.count:
                assert go.remove_oldest() == reference.remove_oldest()
            go._validate()
            expected = reference.snapshot()
            actual = go.snapshot()
            assert actual.count == expected.count
            for field in ("sum", "min", "max", "mean", "variance", "std"):
                assert getattr(actual, field) == pytest.approx(
                    getattr(expected, field), abs=1e-10
                )
    assert go.closed
