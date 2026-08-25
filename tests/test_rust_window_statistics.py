from __future__ import annotations

import random

import pytest

from stream_stats import WindowStatistics
from stream_stats._rust_window_statistics import rust_library_available


pytestmark = pytest.mark.skipif(
    not rust_library_available(), reason="Rust shared library is not built"
)


def test_rust_lifecycle_batch_and_recycling() -> None:
    with WindowStatistics(3, backend="rust") as stats:
        assert stats.add_many([1.0, 2.0, 3.0, 4.0]) == [None, None, None, 1.0]
        assert stats.snapshot().count == 3
        assert stats.sum == 9.0
        stats._validate()
    assert stats.closed
    with pytest.raises(RuntimeError, match="closed"):
        stats.add(5.0)


def test_rust_matches_python_over_random_updates() -> None:
    rng = random.Random(731)
    python = WindowStatistics(31)
    rust = WindowStatistics(31, backend="rust")
    for _ in range(2000):
        value = float(rng.randrange(-20, 21))
        assert rust.add(value) == python.add(value)
        if rng.random() < 0.08 and python.count:
            assert rust.remove_oldest() == python.remove_oldest()
        rust._validate()
        rust_snapshot = rust.snapshot()
        python_snapshot = python.snapshot()
        assert rust_snapshot.count == python_snapshot.count
        for field in ("sum", "min", "max", "mean", "variance", "std"):
            assert getattr(rust_snapshot, field) == pytest.approx(
                getattr(python_snapshot, field), abs=1e-10
            )
