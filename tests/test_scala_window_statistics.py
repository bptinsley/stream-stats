from __future__ import annotations

import pytest

from stream_stats import WindowStatistics
from stream_stats._scala_window_statistics import scala_backend_available


pytestmark = pytest.mark.skipif(
    not scala_backend_available(), reason="Scala worker is not built"
)


def test_scala_worker_batch_queries_and_lifecycle() -> None:
    with WindowStatistics(3, backend="scala") as scala:
        assert scala.add_many([1.0, 2.0, 3.0, 4.0]) == [None, None, None, 1.0]
        assert scala.snapshot().variance == pytest.approx(2.0 / 3.0)
        assert scala.percentile(50) == 3.0
        assert scala.percentile_of(3.0) == pytest.approx(50.0)
        scala._validate()
    assert scala.closed
