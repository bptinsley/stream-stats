from __future__ import annotations

import math
import os
import select
import shutil
import struct
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from statistics import StatisticsError

from ._models import WindowStatisticsSnapshot
from ._window_statistics import _finite_float, _positive_window_size
from .backends import BackendUnavailableError, BackendWorkerError

_HELLO = 0
_CREATE = 1
_ADD = 2
_ADD_MANY = 3
_REMOVE_OLDEST = 4
_REMOVE = 5
_CLEAR = 6
_SNAPSHOT = 7
_PERCENTILE = 8
_PERCENTILE_OF = 9
_VALIDATE = 10
_CLOSE = 11
_SHUTDOWN = 12
_PROTOCOL_VERSION = 1
_MAX_FRAME = 64 * 1024 * 1024


class JVMWindowStatisticsEngine:
    name = "jvm"
    startup_timeout = 10.0
    request_timeout = 5.0
    shutdown_timeout = 2.0

    @classmethod
    def artifact_path(cls) -> Path | None:
        raise NotImplementedError

    @classmethod
    def java_executable(cls) -> str | None:
        configured = os.environ.get("STREAM_STATS_JAVA_EXECUTABLE")
        if configured:
            return configured
        homebrew = Path("/opt/homebrew/opt/openjdk/bin/java")
        if homebrew.is_file():
            return str(homebrew)
        return shutil.which("java")

    @classmethod
    def available(cls) -> bool:
        return cls.artifact_path() is not None and cls.java_executable() is not None

    @classmethod
    def worker_command(cls, executable: str, artifact: Path) -> list[str]:
        return [executable, "-jar", str(artifact)]

    def __init__(self, window_size: int) -> None:
        self.window_size = _positive_window_size(window_size)
        artifact = self.artifact_path()
        executable = self.java_executable()
        if artifact is None or executable is None:
            raise BackendUnavailableError(
                f"{self.name} worker artifact or Java runtime is unavailable"
            )
        self._owner_pid = os.getpid()
        self._closed = False
        self._failed = False
        self._request_id = 0
        self._count_cache = 0
        self._sum_cache = 0.0
        try:
            self._process = subprocess.Popen(
                self.worker_command(executable, artifact),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            hello = self._request(_HELLO, timeout=self.startup_timeout)
            protocol, maximum = struct.unpack("<II", hello)
            if protocol != _PROTOCOL_VERSION or maximum < 1024:
                raise BackendWorkerError(
                    f"{self.name} worker protocol handshake is incompatible"
                )
            response = self._request(_CREATE, struct.pack("<Q", self.window_size))
            (self._handle,) = struct.unpack("<Q", response)
        except Exception:
            self._failed = True
            self._terminate_worker()
            raise

    def _ensure_open(self) -> None:
        if self._closed or self._failed:
            raise RuntimeError("window statistics engine is closed")
        if os.getpid() != self._owner_pid:
            raise RuntimeError(
                f"{self.name} window statistics engine cannot be used after fork"
            )

    def _write_all(self, data: bytes) -> None:
        stream = self._process.stdin
        if stream is None:
            raise BackendWorkerError(f"{self.name} worker stdin is unavailable")
        try:
            stream.write(data)
            stream.flush()
        except (BrokenPipeError, OSError) as exc:
            raise BackendWorkerError(f"{self.name} worker pipe failed") from exc

    def _read_exact(self, length: int, timeout: float) -> bytes:
        stream = self._process.stdout
        if stream is None:
            raise BackendWorkerError(f"{self.name} worker stdout is unavailable")
        deadline = time.monotonic() + timeout
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            wait = deadline - time.monotonic()
            if wait <= 0:
                raise BackendWorkerError(f"{self.name} worker request timed out")
            ready, _, _ = select.select([stream.fileno()], [], [], wait)
            if not ready:
                raise BackendWorkerError(f"{self.name} worker request timed out")
            chunk = os.read(stream.fileno(), remaining)
            if not chunk:
                raise BackendWorkerError(
                    f"{self.name} worker returned EOF or a truncated response"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _request(
        self, command: int, payload: bytes = b"", *, timeout: float | None = None
    ) -> bytes:
        if self._failed:
            raise BackendWorkerError(f"{self.name} worker is unavailable")
        self._request_id += 1
        request = struct.pack("<IB", self._request_id, command) + payload
        if len(request) > _MAX_FRAME:
            raise ValueError("worker request exceeds maximum frame size")
        try:
            self._write_all(struct.pack("<I", len(request)) + request)
            header = self._read_exact(4, timeout or self.request_timeout)
            (length,) = struct.unpack("<I", header)
            if length < 8 or length > _MAX_FRAME:
                raise BackendWorkerError(
                    f"{self.name} worker returned an invalid frame length"
                )
            response = self._read_exact(length, timeout or self.request_timeout)
            request_id, status = struct.unpack_from("<Ii", response)
            if request_id != self._request_id:
                raise BackendWorkerError(
                    f"{self.name} worker response request ID mismatch"
                )
            if status:
                self._raise_status(status)
            return response[8:]
        except (StatisticsError, ValueError, MemoryError):
            raise
        except Exception:
            self._failed = True
            self._terminate_worker()
            raise

    @staticmethod
    def _raise_status(status: int) -> None:
        if status == 1:
            raise ValueError("invalid backend argument")
        if status == 2:
            raise StatisticsError("empty window")
        if status == 3:
            raise ValueError("value is not present in window")
        if status == 4:
            raise MemoryError("backend allocation failed")
        if status == 6:
            raise RuntimeError("window statistics engine is closed")
        raise RuntimeError("backend worker reported an internal error")

    def add(self, value: float) -> float | None:
        self._ensure_open()
        number = _finite_float(value)
        response = self._request(
            _ADD, struct.pack("<Qd", self._handle, number)
        )
        did_evict, evicted = struct.unpack("<Bd", response)
        if did_evict:
            self._sum_cache += number - evicted
        else:
            self._count_cache += 1
            self._sum_cache += number
        return evicted if did_evict else None

    def add_many(self, values: Iterable[float]) -> list[float | None]:
        self._ensure_open()
        normalized = tuple(_finite_float(value) for value in values)
        payload = struct.pack("<QI", self._handle, len(normalized))
        if normalized:
            payload += struct.pack(f"<{len(normalized)}d", *normalized)
        response = self._request(_ADD_MANY, payload)
        (length,) = struct.unpack_from("<I", response)
        if length != len(normalized) or len(response) != 4 + 9 * length:
            self._failed = True
            self._terminate_worker()
            raise BackendWorkerError(f"{self.name} worker returned malformed bulk data")
        evictions = [
            value if flag else None
            for flag, value in (
                struct.unpack_from("<Bd", response, 4 + 9 * index)
                for index in range(length)
            )
        ]
        self._count_cache = min(self.window_size, self._count_cache + length)
        self._sum_cache += sum(normalized) - sum(
            value for value in evictions if value is not None
        )
        if self._count_cache == 0:
            self._sum_cache = 0.0
        return evictions

    def remove_oldest(self) -> float:
        self._ensure_open()
        removed = struct.unpack(
            "<d", self._request(_REMOVE_OLDEST, struct.pack("<Q", self._handle))
        )[0]
        self._count_cache -= 1
        self._sum_cache -= removed
        if self._count_cache == 0:
            self._sum_cache = 0.0
        return removed

    def remove(self, value: float) -> None:
        self._ensure_open()
        number = _finite_float(value)
        self._request(
            _REMOVE, struct.pack("<Qd", self._handle, number)
        )
        self._count_cache -= 1
        self._sum_cache -= number
        if self._count_cache == 0:
            self._sum_cache = 0.0

    def clear(self) -> None:
        self._ensure_open()
        self._request(_CLEAR, struct.pack("<Q", self._handle))
        self._count_cache = 0
        self._sum_cache = 0.0

    def snapshot(self) -> WindowStatisticsSnapshot:
        self._ensure_open()
        response = self._request(_SNAPSHOT, struct.pack("<Q", self._handle))
        count, total, has_values = struct.unpack_from("<QdB", response)
        if not has_values:
            return WindowStatisticsSnapshot(0, 0.0, None, None, None, None, None)
        minimum, maximum, mean, variance, std = struct.unpack_from("<5d", response, 17)
        return WindowStatisticsSnapshot(
            count, total, minimum, maximum, mean, variance, std
        )

    def _nonempty(self) -> WindowStatisticsSnapshot:
        snapshot = self.snapshot()
        if not snapshot.count:
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
        return self._nonempty().min  # type: ignore[return-value]

    @property
    def max(self) -> float:
        return self._nonempty().max  # type: ignore[return-value]

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
        if not math.isfinite(requested):
            raise ValueError("percentile must be finite")
        return struct.unpack(
            "<d",
            self._request(
                _PERCENTILE, struct.pack("<Qd", self._handle, requested)
            ),
        )[0]

    def percentile_of(self, value: float) -> float:
        self._ensure_open()
        return struct.unpack(
            "<d",
            self._request(
                _PERCENTILE_OF,
                struct.pack("<Qd", self._handle, _finite_float(value)),
            ),
        )[0]

    def _validate(self) -> None:
        self._ensure_open()
        self._request(_VALIDATE, struct.pack("<Q", self._handle))

    def _terminate_worker(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=self.shutdown_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self.shutdown_timeout)

    def close(self) -> None:
        if self._closed:
            return
        if os.getpid() == self._owner_pid and not self._failed:
            try:
                self._request(_CLOSE, struct.pack("<Q", self._handle))
                self._request(_SHUTDOWN)
                self._process.wait(timeout=self.shutdown_timeout)
            except Exception:
                self._terminate_worker()
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
