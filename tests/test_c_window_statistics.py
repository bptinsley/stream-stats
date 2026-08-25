import random
from statistics import pvariance

import pytest

from stream_stats import WindowStatistics, get_window_statistics_backend_info

pytestmark = pytest.mark.skipif(
    not get_window_statistics_backend_info("c").available,
    reason="C shared library is not built",
)


def test_c_backend_batch_snapshot_and_lifecycle():
    window = WindowStatistics(3, backend="c")
    assert window.add_many([1, 2, 3, 4]) == [None, None, None, 1.0]
    snapshot = window.snapshot()
    assert snapshot.count == 3
    assert snapshot.mean == 3
    assert snapshot.variance == pytest.approx(2 / 3)
    window._validate()
    window.close()
    with pytest.raises(RuntimeError, match="closed"):
        window.add(5)


def test_c_backend_random_stream_matches_python_engine():
    rng = random.Random(42)
    c_window = WindowStatistics(31, backend="c")
    python_window = WindowStatistics(31)
    expected = []
    for _ in range(2_000):
        value = float(rng.randint(-100, 100))
        assert c_window.add(value) == python_window.add(value)
        if len(expected) == 31:
            expected.pop(0)
        expected.append(value)
        if rng.random() < 0.05:
            target = rng.choice(expected)
            expected.remove(target)
            c_window.remove(target)
            python_window.remove(target)
        c_snapshot = c_window.snapshot()
        python_snapshot = python_window.snapshot()
        assert c_snapshot.count == python_snapshot.count
        for field in ("sum", "min", "max", "mean", "variance", "std"):
            assert getattr(c_snapshot, field) == pytest.approx(
                getattr(python_snapshot, field), abs=1e-10
            )
        c_window._validate()
    assert c_window.variance == pytest.approx(pvariance(expected), abs=1e-10)
