from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol, runtime_checkable

Statistic = str
SUPPORTED_STATISTICS = frozenset(
    {"count", "sum", "min", "max", "mean", "variance", "std"}
)


@runtime_checkable
class Backend(Protocol):
    """Stateful backend instance owned by one Window."""

    name: str

    def push(self, value: float) -> Mapping[str, float | int]: ...

    def reset(self) -> None: ...


class BackendFactory(Protocol):
    def __call__(
        self, window_size: int, statistics: Iterable[Statistic]
    ) -> Backend: ...
