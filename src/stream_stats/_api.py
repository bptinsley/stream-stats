from __future__ import annotations

import math
import os
from collections.abc import Iterable, Iterator

from ._models import WindowSnapshot
from ._protocol import SUPPORTED_STATISTICS
from .backends import create_backend


class Window:
    """Calculate selected statistics over a fixed-size trailing window."""

    def __init__(
        self,
        size: int,
        statistics: Iterable[str] = ("mean",),
        *,
        backend: str | None = None,
    ) -> None:
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError("size must be a positive integer")
        selected = tuple(dict.fromkeys(statistics))
        if not selected:
            raise ValueError("at least one statistic is required")
        unsupported = set(selected) - SUPPORTED_STATISTICS
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"unsupported statistics: {names}")

        self.size = size
        self.statistics = selected
        self.backend_name = backend or os.environ.get("STREAM_STATS_BACKEND", "python")
        self._backend = create_backend(self.backend_name, size, selected)
        self._count = 0

    def push(self, value: float) -> WindowSnapshot:
        if isinstance(value, bool):
            raise TypeError("stream values must be real numbers, not bool")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise TypeError("stream values must be real numbers") from exc
        if not math.isfinite(number):
            raise ValueError("stream values must be finite numbers")
        values = self._backend.push(number)
        self._count = min(self._count + 1, self.size)
        count = int(values.get("count", self._count))
        return WindowSnapshot(
            count=count,
            values={key: item for key, item in values.items() if key != "count"},
        )

    def map(self, values: Iterable[float]) -> Iterator[WindowSnapshot]:
        for value in values:
            yield self.push(value)

    def reset(self) -> None:
        self._backend.reset()
        self._count = 0
