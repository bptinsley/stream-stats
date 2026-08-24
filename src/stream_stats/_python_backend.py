from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable

from ._protocol import Statistic


class PythonBackend:
    """Portable O(window-size) memory reference implementation."""

    name = "python"

    def __init__(self, window_size: int, statistics: Iterable[Statistic]) -> None:
        self.window_size = window_size
        self.statistics = tuple(statistics)
        self._values: deque[float] = deque()
        self._minimums: deque[float] = deque()
        self._maximums: deque[float] = deque()
        self._sum = 0.0
        self._mean = 0.0
        self._m2 = 0.0

    def reset(self) -> None:
        self._values.clear()
        self._minimums.clear()
        self._maximums.clear()
        self._sum = 0.0
        self._mean = 0.0
        self._m2 = 0.0

    def push(self, value: float) -> dict[str, float | int]:
        if not math.isfinite(value):
            raise ValueError("stream values must be finite numbers")

        if len(self._values) == self.window_size:
            expired = self._values.popleft()
            self._sum -= expired
            old_count = self.window_size
            if old_count == 1:
                self._mean = 0.0
                self._m2 = 0.0
            else:
                new_mean = (old_count * self._mean - expired) / (old_count - 1)
                self._m2 -= (expired - self._mean) * (expired - new_mean)
                self._mean = new_mean
            if self._minimums[0] == expired:
                self._minimums.popleft()
            if self._maximums[0] == expired:
                self._maximums.popleft()

        previous_count = len(self._values)
        self._values.append(value)
        self._sum += value
        delta = value - self._mean
        self._mean += delta / (previous_count + 1)
        self._m2 += delta * (value - self._mean)

        while self._minimums and self._minimums[-1] > value:
            self._minimums.pop()
        self._minimums.append(value)
        while self._maximums and self._maximums[-1] < value:
            self._maximums.pop()
        self._maximums.append(value)

        count = len(self._values)
        result: dict[str, float | int] = {}
        for statistic in self.statistics:
            if statistic == "count":
                result[statistic] = count
            elif statistic == "sum":
                result[statistic] = self._sum
            elif statistic == "min":
                result[statistic] = self._minimums[0]
            elif statistic == "max":
                result[statistic] = self._maximums[0]
            elif statistic == "mean":
                result[statistic] = self._mean
            elif statistic in {"variance", "std"}:
                variance = max(0.0, self._m2 / count)
                result[statistic] = (
                    math.sqrt(variance) if statistic == "std" else variance
                )
        return result
