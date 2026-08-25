# cython: language_level=3, boundscheck=False, wraparound=False, initializedcheck=False

from cpython.mem cimport PyMem_Free, PyMem_Realloc
from libc.stdint cimport int32_t, uint8_t, uint32_t, uint64_t
from libc.stddef cimport size_t

import os
from statistics import StatisticsError

from ._models import WindowStatisticsSnapshot
from ._window_statistics import _finite_float, _positive_window_size


cdef extern from "stream_stats_window.h":
    ctypedef struct stream_stats_ws_state:
        pass

    ctypedef int32_t stream_stats_ws_status

    ctypedef struct stream_stats_ws_snapshot:
        uint32_t abi_version
        uint32_t struct_size
        uint64_t count
        double sum
        double min
        double max
        double mean
        double variance
        double std
        uint8_t has_values
        uint8_t reserved[7]

    uint32_t stream_stats_ws_abi_version() noexcept nogil
    const char *stream_stats_ws_last_error() noexcept nogil
    stream_stats_ws_status stream_stats_ws_create(
        uint64_t window_size, stream_stats_ws_state **out
    ) noexcept nogil
    void stream_stats_ws_destroy(stream_stats_ws_state *state) noexcept nogil
    stream_stats_ws_status stream_stats_ws_clear(
        stream_stats_ws_state *state
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_add(
        stream_stats_ws_state *state,
        double value,
        uint8_t *did_evict,
        double *evicted,
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_add_many(
        stream_stats_ws_state *state,
        const double *values,
        uint64_t length,
        uint8_t *did_evict,
        double *evicted,
        uint64_t eviction_capacity,
        stream_stats_ws_snapshot *final_snapshot,
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_remove_oldest(
        stream_stats_ws_state *state, double *removed
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_remove_value(
        stream_stats_ws_state *state, double value
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_snapshot_get(
        const stream_stats_ws_state *state, stream_stats_ws_snapshot *out
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_min(
        const stream_stats_ws_state *state, double *out
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_max(
        const stream_stats_ws_state *state, double *out
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_percentile(
        const stream_stats_ws_state *state, double percentile, double *out
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_percentile_of(
        const stream_stats_ws_state *state, double value, double *out
    ) noexcept nogil
    stream_stats_ws_status stream_stats_ws_validate(
        const stream_stats_ws_state *state
    ) noexcept nogil


cdef int OK = 0
cdef int INVALID_ARGUMENT = 1
cdef int EMPTY_WINDOW = 2
cdef int VALUE_NOT_FOUND = 3
cdef int OUT_OF_MEMORY = 4
cdef int INTERNAL_ERROR = 5
cdef int CLOSED = 6
cdef uint32_t ABI_VERSION = 1


cdef class CythonWindowStatisticsEngine:
    """Compiled direct binding to the canonical recycled-array AVL core."""

    name = "cython"
    cdef stream_stats_ws_state *_state
    cdef bint _closed
    cdef long _owner_pid
    cdef uint64_t _count_cache
    cdef double _sum_cache
    cdef double *_input_buffer
    cdef double *_evicted_buffer
    cdef uint8_t *_flag_buffer
    cdef Py_ssize_t _buffer_capacity
    cdef readonly object window_size

    def __cinit__(self, window_size):
        self._state = NULL
        self._closed = False
        self._count_cache = 0
        self._sum_cache = 0.0
        self._input_buffer = NULL
        self._evicted_buffer = NULL
        self._flag_buffer = NULL
        self._buffer_capacity = 0
        self.window_size = _positive_window_size(window_size)
        self._owner_pid = os.getpid()
        if stream_stats_ws_abi_version() != ABI_VERSION:
            raise RuntimeError("Cython backend ABI version is incompatible")
        self._check(stream_stats_ws_create(self.window_size, &self._state))

    cdef void _ensure_open(self) except *:
        if self._closed:
            raise RuntimeError("window statistics engine is closed")
        if os.getpid() != self._owner_pid:
            raise RuntimeError(
                "cython window statistics engine cannot be used after fork"
            )

    cdef void _check(self, stream_stats_ws_status status) except *:
        cdef const char *raw
        cdef object detail
        if status == OK:
            return
        raw = stream_stats_ws_last_error()
        detail = raw.decode("utf-8") if raw != NULL else "Cython backend failure"
        if status == INVALID_ARGUMENT:
            raise ValueError(detail)
        if status == EMPTY_WINDOW:
            raise StatisticsError(detail)
        if status == VALUE_NOT_FOUND:
            raise ValueError(detail)
        if status == OUT_OF_MEMORY:
            raise MemoryError(detail)
        if status == INTERNAL_ERROR or status == CLOSED:
            raise RuntimeError(detail)
        raise RuntimeError(f"unknown Cython backend status {status}: {detail}")

    cdef stream_stats_ws_snapshot _native_snapshot(self) except *:
        cdef stream_stats_ws_snapshot snapshot
        snapshot.abi_version = ABI_VERSION
        snapshot.struct_size = sizeof(stream_stats_ws_snapshot)
        self._check(stream_stats_ws_snapshot_get(self._state, &snapshot))
        return snapshot

    cdef void _ensure_batch_capacity(self, Py_ssize_t length) except *:
        cdef void *resized
        if length <= self._buffer_capacity:
            return
        resized = PyMem_Realloc(self._input_buffer, length * sizeof(double))
        if resized == NULL:
            raise MemoryError("Cython input buffer allocation failed")
        self._input_buffer = <double *>resized
        resized = PyMem_Realloc(self._evicted_buffer, length * sizeof(double))
        if resized == NULL:
            raise MemoryError("Cython eviction buffer allocation failed")
        self._evicted_buffer = <double *>resized
        resized = PyMem_Realloc(self._flag_buffer, length * sizeof(uint8_t))
        if resized == NULL:
            raise MemoryError("Cython flag buffer allocation failed")
        self._flag_buffer = <uint8_t *>resized
        self._buffer_capacity = length

    def add(self, value):
        cdef double number
        cdef double evicted = 0.0
        cdef uint8_t did_evict = 0
        self._ensure_open()
        number = _finite_float(value)
        self._check(stream_stats_ws_add(self._state, number, &did_evict, &evicted))
        if not did_evict:
            self._count_cache += 1
            self._sum_cache += number
        else:
            self._sum_cache += number - evicted
        return evicted if did_evict else None

    def add_many(self, values):
        cdef const double[::1] direct_values
        cdef object normalized = None
        cdef Py_ssize_t length, index
        cdef const double *input_values = NULL
        cdef stream_stats_ws_snapshot final_snapshot
        cdef stream_stats_ws_status status
        cdef bint direct = False
        self._ensure_open()
        try:
            direct_values = values
            length = direct_values.shape[0]
            direct = True
        except (TypeError, ValueError, BufferError):
            normalized = tuple(_finite_float(value) for value in values)
            length = len(normalized)
        if length == 0:
            return []
        self._ensure_batch_capacity(length)
        if direct:
            input_values = &direct_values[0]
        else:
            for index in range(length):
                self._input_buffer[index] = normalized[index]
            input_values = self._input_buffer
        final_snapshot.abi_version = ABI_VERSION
        final_snapshot.struct_size = sizeof(stream_stats_ws_snapshot)
        with nogil:
            status = stream_stats_ws_add_many(
                self._state,
                input_values,
                length,
                self._flag_buffer,
                self._evicted_buffer,
                length,
                &final_snapshot,
            )
        self._check(status)
        self._count_cache = final_snapshot.count
        self._sum_cache = final_snapshot.sum
        return [
            self._evicted_buffer[index] if self._flag_buffer[index] else None
            for index in range(length)
        ]

    def remove_oldest(self):
        cdef double removed = 0.0
        self._ensure_open()
        self._check(stream_stats_ws_remove_oldest(self._state, &removed))
        self._count_cache -= 1
        self._sum_cache -= removed
        if self._count_cache == 0:
            self._sum_cache = 0.0
        return removed

    def remove(self, value):
        cdef double number
        self._ensure_open()
        number = _finite_float(value)
        self._check(stream_stats_ws_remove_value(self._state, number))
        self._count_cache -= 1
        self._sum_cache -= number
        if self._count_cache == 0:
            self._sum_cache = 0.0

    def clear(self):
        self._ensure_open()
        self._check(stream_stats_ws_clear(self._state))
        self._count_cache = 0
        self._sum_cache = 0.0

    def snapshot(self):
        cdef stream_stats_ws_snapshot native
        self._ensure_open()
        native = self._native_snapshot()
        if not native.has_values:
            return WindowStatisticsSnapshot(0, 0.0, None, None, None, None, None)
        return WindowStatisticsSnapshot(
            native.count,
            native.sum,
            native.min,
            native.max,
            native.mean,
            native.variance,
            native.std,
        )

    @property
    def count(self):
        self._ensure_open()
        return self._count_cache

    @property
    def sum(self):
        self._ensure_open()
        return self._sum_cache

    cdef stream_stats_ws_snapshot _nonempty(self) except *:
        cdef stream_stats_ws_snapshot native = self._native_snapshot()
        if not native.has_values:
            raise StatisticsError("empty window")
        return native

    @property
    def min(self):
        cdef double result = 0.0
        self._ensure_open()
        self._check(stream_stats_ws_min(self._state, &result))
        return result

    @property
    def max(self):
        cdef double result = 0.0
        self._ensure_open()
        self._check(stream_stats_ws_max(self._state, &result))
        return result

    @property
    def mean(self):
        self._ensure_open()
        return self._nonempty().mean

    @property
    def variance(self):
        self._ensure_open()
        return self._nonempty().variance

    @property
    def std(self):
        self._ensure_open()
        return self._nonempty().std

    def percentile(self, percentile):
        cdef double requested
        cdef double result = 0.0
        self._ensure_open()
        if isinstance(percentile, bool):
            raise TypeError("percentile must be a real number, not bool")
        try:
            requested = float(percentile)
        except (TypeError, ValueError) as exc:
            raise TypeError("percentile must be a real number") from exc
        self._check(stream_stats_ws_percentile(self._state, requested, &result))
        return result

    def percentile_of(self, value):
        cdef double result = 0.0
        self._ensure_open()
        self._check(
            stream_stats_ws_percentile_of(
                self._state, _finite_float(value), &result
            )
        )
        return result

    def _validate(self):
        self._ensure_open()
        self._check(stream_stats_ws_validate(self._state))

    def close(self):
        if self._closed:
            return
        if os.getpid() == self._owner_pid and self._state != NULL:
            stream_stats_ws_destroy(self._state)
        self._state = NULL
        self._closed = True

    @property
    def closed(self):
        return bool(self._closed)

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __dealloc__(self):
        if not self._closed and self._state != NULL:
            if os.getpid() == self._owner_pid:
                stream_stats_ws_destroy(self._state)
            self._state = NULL
            self._closed = True
        PyMem_Free(self._input_buffer)
        PyMem_Free(self._evicted_buffer)
        PyMem_Free(self._flag_buffer)
        self._input_buffer = NULL
        self._evicted_buffer = NULL
        self._flag_buffer = NULL
