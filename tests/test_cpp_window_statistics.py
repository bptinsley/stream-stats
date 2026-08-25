from __future__ import annotations

import random

import pytest

from stream_stats import WindowStatistics
from stream_stats._cpp_window_statistics import cpp_library_available


pytestmark = pytest.mark.skipif(
    not cpp_library_available(), reason="C++ shared library is not built"
)


def test_cpp_random_updates_match_python() -> None:
    rng = random.Random(9127)
    reference = WindowStatistics(47)
    cpp = WindowStatistics(47, backend="cpp")
    for _ in range(2000):
        value = float(rng.randrange(-50, 51))
        assert cpp.add(value) == reference.add(value)
        if rng.random() < 0.07 and reference.count:
            assert cpp.remove_oldest() == reference.remove_oldest()
        cpp._validate()
        expected = reference.snapshot()
        actual = cpp.snapshot()
        assert actual.count == expected.count
        for field in ("sum", "min", "max", "mean", "variance", "std"):
            assert getattr(actual, field) == pytest.approx(
                getattr(expected, field), abs=1e-10
            )
