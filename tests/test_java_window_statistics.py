from __future__ import annotations

import pytest

from stream_stats import WindowStatistics
from stream_stats._java_window_statistics import java_backend_available


pytestmark = pytest.mark.skipif(
    not java_backend_available(), reason="Java worker is not built"
)


def test_java_worker_batch_queries_and_lifecycle() -> None:
    with WindowStatistics(3, backend="java") as java:
        assert java.add_many([1.0, 2.0, 3.0, 4.0]) == [None, None, None, 1.0]
        assert java.snapshot().variance == pytest.approx(2.0 / 3.0)
        assert java.percentile(50) == 3.0
        assert java.percentile_of(3.0) == pytest.approx(50.0)
        java._validate()
    assert java.closed
