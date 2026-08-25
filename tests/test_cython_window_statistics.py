from __future__ import annotations

from array import array
import random

import pytest

from stream_stats import WindowStatistics, get_window_statistics_backend_info


pytestmark = pytest.mark.skipif(
    not get_window_statistics_backend_info("cython").available,
    reason="Cython extension is not built",
)


def test_cython_random_updates_match_python() -> None:
    rng = random.Random(811)
    reference = WindowStatistics(37)
    with WindowStatistics(37, backend="cython") as cython:
        for _ in range(2_000):
            value = float(rng.randrange(-40, 41))
            assert cython.add(value) == reference.add(value)
            if rng.random() < 0.08 and reference.count:
                assert cython.remove_oldest() == reference.remove_oldest()
            cython._validate()
            actual = cython.snapshot()
            expected = reference.snapshot()
            assert actual.count == expected.count
            for field in ("sum", "min", "max", "mean", "variance", "std"):
                assert getattr(actual, field) == pytest.approx(
                    getattr(expected, field), abs=1e-10
                )


def test_cython_batch_and_lifecycle() -> None:
    window = WindowStatistics(3, backend="cython")
    assert window.add_many([1, 2, 3, 4]) == [None, None, None, 1.0]
    assert window.count == 3
    window.close()
    window.close()
    with pytest.raises(RuntimeError, match="closed"):
        window.add(5)


def test_cython_batch_accepts_and_reuses_contiguous_double_buffers() -> None:
    with WindowStatistics(4, backend="cython") as window:
        assert window.add_many(array("d", [1.0, 2.0])) == [None, None]
        assert window.add_many(memoryview(array("d", [3.0, 4.0, 5.0]))) == [
            None,
            None,
            1.0,
        ]
        assert window.count == 4
        assert window.sum == pytest.approx(14.0)
        assert window.snapshot().sum == pytest.approx(window.sum)


def test_cython_double_buffer_is_validated_before_mutation() -> None:
    with WindowStatistics(4, backend="cython") as window:
        window.add_many([1.0, 2.0])
        before = window.snapshot()
        with pytest.raises(ValueError):
            window.add_many(array("d", [3.0, float("nan"), 4.0]))
        assert window.snapshot() == before


def test_cython_scalar_caches_follow_all_mutations() -> None:
    with WindowStatistics(3, backend="cython") as window:
        window.add_many([1.0, 2.0, 3.0, 4.0])
        assert (window.count, window.sum) == (3, 9.0)
        assert window.remove_oldest() == 2.0
        assert (window.count, window.sum) == (2, 7.0)
        window.remove(3.0)
        assert (window.count, window.sum) == (1, 4.0)
        window.clear()
        assert (window.count, window.sum) == (0, 0.0)
