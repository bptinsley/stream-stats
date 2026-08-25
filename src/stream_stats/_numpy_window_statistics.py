from __future__ import annotations

import math
from statistics import StatisticsError

import numpy as np

from ._models import WindowStatisticsSnapshot
from ._window_statistics import _finite_float, _positive_window_size


class NumpyWindowStatistics:
    """Simple NumPy baseline that recalculates statistics from the full window."""

    def __init__(self, window_size: int) -> None:
        self.window_size = _positive_window_size(window_size)
        self._values = np.empty(0, dtype=np.float64)
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("window statistics engine is closed")

    def add(self, value: float) -> float | None:
        self._ensure_open()
        number = _finite_float(value)
        outgoing = float(self._values[0]) if self.count == self.window_size else None
        updated = np.append(self._values, number)
        if updated.size > self.window_size:
            updated = updated[-self.window_size :].copy()
        self._values = updated
        return outgoing

    def add_many(self, values) -> list[float | None]:
        self._ensure_open()
        normalized = tuple(_finite_float(value) for value in values)
        return [self.add(value) for value in normalized]

    def remove_oldest(self) -> float:
        self._require_values()
        outgoing = float(self._values[0])
        self._values = self._values[1:].copy()
        return outgoing

    def remove(self, value: float) -> None:
        self._ensure_open()
        number = _finite_float(value)
        matches = np.flatnonzero(self._values == number)
        if matches.size == 0:
            raise ValueError(f"value is not present in the window: {number!r}")
        self._values = np.delete(self._values, int(matches[0]))

    def clear(self) -> None:
        self._ensure_open()
        self._values = np.empty(0, dtype=np.float64)

    def _require_values(self) -> None:
        self._ensure_open()
        if self._values.size == 0:
            raise StatisticsError("no values in the window")

    @property
    def count(self) -> int:
        self._ensure_open()
        return int(self._values.size)

    @property
    def sum(self) -> float:
        self._ensure_open()
        return float(np.sum(self._values, dtype=np.float64))

    @property
    def min(self) -> float:
        self._require_values()
        return float(np.min(self._values))

    @property
    def max(self) -> float:
        self._require_values()
        return float(np.max(self._values))

    @property
    def mean(self) -> float:
        self._require_values()
        return float(np.mean(self._values, dtype=np.float64))

    @property
    def variance(self) -> float:
        self._require_values()
        return float(np.var(self._values, dtype=np.float64, ddof=0))

    @property
    def std(self) -> float:
        self._require_values()
        return float(np.std(self._values, dtype=np.float64, ddof=0))

    @property
    def standard_deviation(self) -> float:
        return self.std

    def percentile(self, percentile: float) -> float:
        self._require_values()
        if isinstance(percentile, bool):
            raise TypeError("percentile must be a real number, not bool")
        try:
            requested = float(percentile)
        except (TypeError, ValueError) as exc:
            raise TypeError("percentile must be a real number") from exc
        if not math.isfinite(requested) or not 0.0 <= requested <= 100.0:
            raise ValueError("percentile must be finite and between 0 and 100")
        return float(np.percentile(self._values, requested, method="linear"))

    def percentile_of(self, value: float) -> float:
        self._require_values()
        number = _finite_float(value)
        less = int(np.count_nonzero(self._values < number))
        equal = int(np.count_nonzero(self._values == number))
        return 100.0 * (less + 0.5 * equal) / self.count

    def snapshot(self) -> WindowStatisticsSnapshot:
        self._ensure_open()
        if self._values.size == 0:
            return WindowStatisticsSnapshot(0, 0.0, None, None, None, None, None)
        variance = self.variance
        return WindowStatisticsSnapshot(
            count=self.count,
            sum=self.sum,
            min=self.min,
            max=self.max,
            mean=self.mean,
            variance=variance,
            std=float(np.sqrt(variance)),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._values = np.empty(0, dtype=np.float64)
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _values_in_order(self) -> list[float]:
        return self._values.tolist()
