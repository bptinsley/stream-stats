from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from ._c_window_statistics import CWindowStatisticsEngine, _configure_library


def _library_names() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("libstream_stats_cpp.dylib",)
    if sys.platform == "win32":
        return ("stream_stats_cpp.dll", "libstream_stats_cpp.dll")
    return ("libstream_stats_cpp.so",)


def find_cpp_library() -> Path | None:
    configured = os.environ.get("STREAM_STATS_CPP_LIBRARY")
    candidates = [Path(configured)] if configured else []
    package_directory = Path(__file__).resolve().parent
    repository = package_directory.parents[1]
    for name in _library_names():
        candidates.extend(
            (
                package_directory / "lib" / name,
                repository / "backends" / "cpp" / "build" / name,
            )
        )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def cpp_library_available() -> bool:
    return find_cpp_library() is not None


_library: ctypes.CDLL | None = None


def _load_cpp_library() -> ctypes.CDLL:
    global _library
    if _library is not None:
        return _library
    path = find_cpp_library()
    if path is None:
        raise RuntimeError("C++ backend shared library is not built or installed")
    _library = _configure_library(path, "C++")
    return _library


class CppWindowStatisticsEngine(CWindowStatisticsEngine):
    name = "cpp"

    @staticmethod
    def _get_library() -> ctypes.CDLL:
        return _load_cpp_library()
