from statistics import StatisticsError

import pytest

from stream_stats import (
    BackendUnavailableError,
    WindowStatistics,
    WindowStatisticsSnapshot,
    get_window_statistics_backend_info,
    list_window_statistics_backends,
)


def test_default_facade_uses_python_engine_and_preserves_private_diagnostics():
    window = WindowStatistics(3)
    assert window.backend_name == "python"
    assert window._engine.name == "python"
    window.add_many([3, 1, 2])
    window._validate()
    assert window._values_in_order() == [3, 1, 2]


def test_snapshot_returns_all_aggregates_in_one_value():
    window = WindowStatistics(3)
    assert window.snapshot() == WindowStatisticsSnapshot(
        count=0,
        sum=0.0,
        min=None,
        max=None,
        mean=None,
        variance=None,
        std=None,
    )
    window.add_many([1, 2, 3])
    snapshot = window.snapshot()
    assert snapshot.count == 3
    assert snapshot.sum == 6
    assert snapshot.min == 1
    assert snapshot.max == 3
    assert snapshot.mean == 2
    assert snapshot.variance == pytest.approx(2 / 3)
    assert snapshot.std == pytest.approx((2 / 3) ** 0.5)


def test_add_many_validates_complete_batch_before_mutation():
    window = WindowStatistics(3)
    window.add(9)
    with pytest.raises(ValueError):
        window.add_many([1, float("nan"), 2])
    assert window._values_in_order() == [9]


def test_add_many_returns_one_eviction_result_per_input():
    window = WindowStatistics(2)
    assert window.add_many([1, 2, 3, 4]) == [None, None, 1.0, 2.0]


def test_negative_zero_is_normalized():
    window = WindowStatistics(2)
    window.add(-0.0)
    assert window.min == 0.0
    assert window.min.hex() == "0x0.0p+0"
    assert window.remove_oldest().hex() == "0x0.0p+0"


def test_close_is_idempotent_and_context_manager_closes():
    with WindowStatistics(2) as window:
        window.add(1)
        assert not window.closed
    assert window.closed
    window.close()
    with pytest.raises(RuntimeError, match="closed"):
        window.add(2)
    with pytest.raises(RuntimeError, match="closed"):
        _ = window.count


def test_empty_behavior_remains_compatible_through_facade():
    window = WindowStatistics(2)
    assert window.count == 0
    assert window.sum == 0
    with pytest.raises(StatisticsError):
        _ = window.mean


def test_window_statistics_backend_discovery():
    infos = {info.name: info for info in list_window_statistics_backends()}
    assert infos["python"].available
    assert "rust" in infos
    if not infos["rust"].available:
        assert "stream-stats-rust" in infos["rust"].detail
    assert not get_window_statistics_backend_info("missing").available


def test_unavailable_engine_never_falls_back():
    with pytest.raises(BackendUnavailableError, match="stream-stats-assembly"):
        WindowStatistics(2, backend="assembly")
