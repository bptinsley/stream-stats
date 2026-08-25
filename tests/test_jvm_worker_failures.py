from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stream_stats import BackendWorkerError
from stream_stats._jvm_window_statistics import JVMWindowStatisticsEngine


_FAKE_WORKER = r"""
import os, struct, sys, time
mode = sys.argv[1]
def read_exact(n):
    data = b''
    while len(data) < n:
        part = os.read(0, n - len(data))
        if not part: raise SystemExit
        data += part
    return data
def request():
    size = struct.unpack('<I', read_exact(4))[0]
    return read_exact(size)
def respond(request_id, payload=b'', status=0):
    body = struct.pack('<Ii', request_id, status) + payload
    os.write(1, struct.pack('<I', len(body)) + body)
hello = request()
respond(struct.unpack_from('<I', hello)[0], struct.pack('<II', 1, 64 * 1024 * 1024))
create = request()
request_id = struct.unpack_from('<I', create)[0]
if mode == 'timeout':
    time.sleep(5)
elif mode == 'truncated':
    os.write(1, struct.pack('<I', 8) + b'xx')
elif mode == 'mismatch':
    respond(request_id + 1, struct.pack('<Q', 1))
"""


class _FaultWorker(JVMWindowStatisticsEngine):
    name = "fault-test"
    request_timeout = 0.05
    shutdown_timeout = 0.05
    mode = "timeout"

    @classmethod
    def artifact_path(cls) -> Path | None:
        return Path(__file__)

    @classmethod
    def java_executable(cls) -> str | None:
        return sys.executable

    @classmethod
    def worker_command(cls, executable: str, artifact: Path) -> list[str]:
        return [executable, "-c", _FAKE_WORKER, cls.mode]


@pytest.mark.parametrize("mode", ["timeout", "truncated", "mismatch"])
def test_worker_protocol_failures_are_detected_and_not_replayed(mode: str) -> None:
    _FaultWorker.mode = mode
    with pytest.raises(BackendWorkerError):
        _FaultWorker(4)
