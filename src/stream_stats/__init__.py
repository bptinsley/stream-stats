"""Windowed statistics over streaming data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._api import Window
from ._models import WindowSnapshot, WindowStatisticsSnapshot
from ._window_statistics import WindowStatistics
from .backends import (
    BackendInfo,
    BackendUnavailableError,
    BackendWorkerError,
    get_backend_info,
    list_backends,
)
from .window_statistics_backends import (
    get_window_statistics_backend_info,
    list_window_statistics_backends,
    register_window_statistics_backend,
)

if TYPE_CHECKING:
    from ._numpy_window_statistics import NumpyWindowStatistics

__all__ = [
    "BackendInfo",
    "BackendUnavailableError",
    "BackendWorkerError",
    "Window",
    "WindowStatistics",
    "WindowStatisticsSnapshot",
    "WindowSnapshot",
    "get_backend_info",
    "get_window_statistics_backend_info",
    "list_backends",
    "list_window_statistics_backends",
    "register_window_statistics_backend",
]


def __getattr__(name: str) -> Any:
    if name != "NumpyWindowStatistics":
        raise AttributeError(name)
    try:
        from ._numpy_window_statistics import NumpyWindowStatistics
    except ModuleNotFoundError as exc:
        if exc.name == "numpy":
            raise ImportError(
                "NumpyWindowStatistics requires the optional 'baseline' extra; "
                "install stream-stats[baseline]"
            ) from exc
        raise
    return NumpyWindowStatistics

__version__ = "0.1.0"
