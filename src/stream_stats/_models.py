from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WindowSnapshot(Mapping[str, float | int]):
    """Immutable statistics for the window after one input value."""

    count: int
    values: Mapping[str, float | int]

    def __getitem__(self, key: str) -> float | int:
        if key == "count":
            return self.count
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        yield "count"
        yield from self.values

    def __len__(self) -> int:
        return 1 + len(self.values)

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def as_dict(self) -> dict[str, float | int]:
        return dict(self)


@dataclass(frozen=True, slots=True)
class WindowStatisticsSnapshot:
    """All constant-time statistics captured after one engine operation."""

    count: int
    sum: float
    min: float | None
    max: float | None
    mean: float | None
    variance: float | None
    std: float | None
