"""Windowed statistics over streaming data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._api import Window
from ._models import WindowSnapshot
from ._window_statistics import WindowStatistics
from .backends import (
    BackendInfo,
    BackendUnavailableError,
    get_backend_info,
    list_backends,
)

if TYPE_CHECKING:
    from ._numpy_window_statistics import NumpyWindowStatistics

__all__ = [
    "BackendInfo",
    "BackendUnavailableError",
    "Window",
    "WindowStatistics",
    "WindowSnapshot",
    "get_backend_info",
    "list_backends",
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
