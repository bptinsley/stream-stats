from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from statistics import StatisticsError

from ._models import WindowStatisticsSnapshot
from ._window_statistics import _finite_float, _positive_window_size

_ABI_VERSION = 1
_OK = 0
_INVALID_ARGUMENT = 1
_EMPTY_WINDOW = 2
_VALUE_NOT_FOUND = 3
_OUT_OF_MEMORY = 4
_INTERNAL_ERROR = 5
_CLOSED = 6


class _CSnapshot(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("count", ctypes.c_uint64),
        ("sum", ctypes.c_double),
        ("min", ctypes.c_double),
        ("max", ctypes.c_double),
        ("mean", ctypes.c_double),
        ("variance", ctypes.c_double),
        ("std", ctypes.c_double),
        ("has_values", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 7),
    ]


def _library_names() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return ("libstream_stats_c.dylib",)
    if sys.platform == "win32":
        return ("stream_stats_c.dll", "libstream_stats_c.dll")
    return ("libstream_stats_c.so",)


def _library_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = os.environ.get("STREAM_STATS_C_LIBRARY")
    if configured:
        candidates.append(Path(configured))
    package_directory = Path(__file__).resolve().parent
    repository = package_directory.parents[1]
    for name in _library_names():
        candidates.append(package_directory / "lib" / name)
        candidates.append(repository / "backends" / "c" / "build" / name)
    return tuple(candidates)


def find_c_library() -> Path | None:
    for candidate in _library_candidates():
        if candidate.is_file():
            return candidate
    return None


def c_library_available() -> bool:
    return find_c_library() is not None


_library: ctypes.CDLL | None = None


def _configure_library(path: Path, backend_name: str) -> ctypes.CDLL:
    library = ctypes.CDLL(str(path))
    state = ctypes.c_void_p
    status = ctypes.c_int32
    library.stream_stats_ws_abi_version.restype = ctypes.c_uint32
    library.stream_stats_ws_last_error.restype = ctypes.c_char_p
    library.stream_stats_ws_create.argtypes = [ctypes.c_uint64, ctypes.POINTER(state)]
    library.stream_stats_ws_create.restype = status
    library.stream_stats_ws_destroy.argtypes = [state]
    library.stream_stats_ws_destroy.restype = None
    library.stream_stats_ws_clear.argtypes = [state]
    library.stream_stats_ws_clear.restype = status
    library.stream_stats_ws_add.argtypes = [
        state,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_double),
    ]
    library.stream_stats_ws_add.restype = status
    library.stream_stats_ws_add_many.argtypes = [
        state,
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_double),
        ctypes.c_uint64,
        ctypes.POINTER(_CSnapshot),
    ]
    library.stream_stats_ws_add_many.restype = status
    library.stream_stats_ws_remove_oldest.argtypes = [
        state,
        ctypes.POINTER(ctypes.c_double),
    ]
    library.stream_stats_ws_remove_oldest.restype = status
    library.stream_stats_ws_remove_value.argtypes = [state, ctypes.c_double]
    library.stream_stats_ws_remove_value.restype = status
    library.stream_stats_ws_snapshot_get.argtypes = [
        state,
        ctypes.POINTER(_CSnapshot),
    ]
    library.stream_stats_ws_snapshot_get.restype = status
    library.stream_stats_ws_count.argtypes = [state, ctypes.POINTER(ctypes.c_uint64)]
    library.stream_stats_ws_count.restype = status
    library.stream_stats_ws_sum.argtypes = [state, ctypes.POINTER(ctypes.c_double)]
    library.stream_stats_ws_sum.restype = status
    library.stream_stats_ws_min.argtypes = [state, ctypes.POINTER(ctypes.c_double)]
    library.stream_stats_ws_min.restype = status
    library.stream_stats_ws_max.argtypes = [state, ctypes.POINTER(ctypes.c_double)]
    library.stream_stats_ws_max.restype = status
    library.stream_stats_ws_percentile.argtypes = [
        state,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
    ]
    library.stream_stats_ws_percentile.restype = status
    library.stream_stats_ws_percentile_of.argtypes = [
        state,
        ctypes.c_double,
        ctypes.POINTER(ctypes.c_double),
    ]
    library.stream_stats_ws_percentile_of.restype = status
    library.stream_stats_ws_validate.argtypes = [state]
    library.stream_stats_ws_validate.restype = status
    if library.stream_stats_ws_abi_version() != _ABI_VERSION:
        raise RuntimeError(f"{backend_name} backend ABI version is incompatible")
    return library


def _load_library() -> ctypes.CDLL:
    global _library
    if _library is not None:
        return _library
    path = find_c_library()
    if path is None:
        raise RuntimeError("C backend shared library is not built or installed")
    library = _configure_library(path, "C")
    _library = library
    return library


class CWindowStatisticsEngine:
    name = "c"

    @staticmethod
    def _get_library() -> ctypes.CDLL:
        return _load_library()

    def __init__(self, window_size: int) -> None:
        self.window_size = _positive_window_size(window_size)
        self._library = self._get_library()
        self._state = ctypes.c_void_p()
        self._owner_pid = os.getpid()
        self._closed = False
        self._count_cache = 0
        self._sum_cache = 0.0
        self._check(
            self._library.stream_stats_ws_create(
                self.window_size, ctypes.byref(self._state)
            )
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("window statistics engine is closed")
        if os.getpid() != self._owner_pid:
            raise RuntimeError(
                f"{self.name} window statistics engine cannot be used after fork"
            )

    def _check(self, status: int) -> None:
        if status == _OK:
            return
        detail_bytes = self._library.stream_stats_ws_last_error()
        detail = detail_bytes.decode() if detail_bytes else f"{self.name} backend failure"
        if status == _INVALID_ARGUMENT:
            raise ValueError(detail)
        if status == _EMPTY_WINDOW:
            raise StatisticsError(detail)
        if status == _VALUE_NOT_FOUND:
            raise ValueError(detail)
        if status == _OUT_OF_MEMORY:
            raise MemoryError(detail)
        if status in {_INTERNAL_ERROR, _CLOSED}:
            raise RuntimeError(detail)
        raise RuntimeError(f"unknown {self.name} backend status {status}: {detail}")

    @staticmethod
    def _new_snapshot() -> _CSnapshot:
        snapshot = _CSnapshot()
        snapshot.abi_version = _ABI_VERSION
        snapshot.struct_size = ctypes.sizeof(_CSnapshot)
        return snapshot

    def add(self, value: float) -> float | None:
        self._ensure_open()
        number = _finite_float(value)
        did_evict = ctypes.c_uint8()
        evicted = ctypes.c_double()
        self._check(
            self._library.stream_stats_ws_add(
                self._state, number, ctypes.byref(did_evict), ctypes.byref(evicted)
            )
        )
        if did_evict.value:
            self._sum_cache += number - evicted.value
        else:
            self._count_cache += 1
            self._sum_cache += number
        return evicted.value if did_evict.value else None

    def add_many(self, values: Iterable[float]) -> list[float | None]:
        self._ensure_open()
        normalized = tuple(_finite_float(value) for value in values)
        if not normalized:
            return []
        length = len(normalized)
        value_array = (ctypes.c_double * length)(*normalized)
        flags = (ctypes.c_uint8 * length)()
        evicted = (ctypes.c_double * length)()
        snapshot = self._new_snapshot()
        self._check(
            self._library.stream_stats_ws_add_many(
                self._state,
                value_array,
                length,
                flags,
                evicted,
                length,
                ctypes.byref(snapshot),
            )
        )
        self._count_cache = int(snapshot.count)
        self._sum_cache = float(snapshot.sum)
        return [evicted[index] if flags[index] else None for index in range(length)]

    def remove_oldest(self) -> float:
        self._ensure_open()
        removed = ctypes.c_double()
        self._check(
            self._library.stream_stats_ws_remove_oldest(
                self._state, ctypes.byref(removed)
            )
        )
        self._count_cache -= 1
        self._sum_cache -= removed.value
        if self._count_cache == 0:
            self._sum_cache = 0.0
        return removed.value

    def remove(self, value: float) -> None:
        self._ensure_open()
        number = _finite_float(value)
        self._check(
            self._library.stream_stats_ws_remove_value(
                self._state, number
            )
        )
        self._count_cache -= 1
        self._sum_cache -= number
        if self._count_cache == 0:
            self._sum_cache = 0.0

    def clear(self) -> None:
        self._ensure_open()
        self._check(self._library.stream_stats_ws_clear(self._state))
        self._count_cache = 0
        self._sum_cache = 0.0

    def snapshot(self) -> WindowStatisticsSnapshot:
        self._ensure_open()
        native = self._new_snapshot()
        self._check(
            self._library.stream_stats_ws_snapshot_get(
                self._state, ctypes.byref(native)
            )
        )
        if not native.has_values:
            return WindowStatisticsSnapshot(0, 0.0, None, None, None, None, None)
        return WindowStatisticsSnapshot(
            count=native.count,
            sum=native.sum,
            min=native.min,
            max=native.max,
            mean=native.mean,
            variance=native.variance,
            std=native.std,
        )

    def _nonempty(self) -> WindowStatisticsSnapshot:
        snapshot = self.snapshot()
        if snapshot.count == 0:
            raise StatisticsError("empty window")
        return snapshot

    @property
    def count(self) -> int:
        self._ensure_open()
        return self._count_cache

    @property
    def sum(self) -> float:
        self._ensure_open()
        return self._sum_cache

    @property
    def min(self) -> float:
        self._ensure_open()
        result = ctypes.c_double()
        self._check(self._library.stream_stats_ws_min(self._state, ctypes.byref(result)))
        return result.value

    @property
    def max(self) -> float:
        self._ensure_open()
        result = ctypes.c_double()
        self._check(self._library.stream_stats_ws_max(self._state, ctypes.byref(result)))
        return result.value

    @property
    def mean(self) -> float:
        return self._nonempty().mean  # type: ignore[return-value]

    @property
    def variance(self) -> float:
        return self._nonempty().variance  # type: ignore[return-value]

    @property
    def std(self) -> float:
        return self._nonempty().std  # type: ignore[return-value]

    def percentile(self, percentile: float) -> float:
        self._ensure_open()
        if isinstance(percentile, bool):
            raise TypeError("percentile must be a real number, not bool")
        try:
            requested = float(percentile)
        except (TypeError, ValueError) as exc:
            raise TypeError("percentile must be a real number") from exc
        result = ctypes.c_double()
        self._check(
            self._library.stream_stats_ws_percentile(
                self._state, requested, ctypes.byref(result)
            )
        )
        return result.value

    def percentile_of(self, value: float) -> float:
        self._ensure_open()
        result = ctypes.c_double()
        self._check(
            self._library.stream_stats_ws_percentile_of(
                self._state, _finite_float(value), ctypes.byref(result)
            )
        )
        return result.value

    def _validate(self) -> None:
        self._ensure_open()
        self._check(self._library.stream_stats_ws_validate(self._state))

    def close(self) -> None:
        if self._closed:
            return
        if os.getpid() == self._owner_pid and self._state:
            self._library.stream_stats_ws_destroy(self._state)
        self._state = ctypes.c_void_p()
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
