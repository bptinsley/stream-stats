from __future__ import annotations

import json
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    backend: str
    samples: int
    repeats: int
    median_elapsed_ns: int
    median_samples_per_second: float
    max_peak_rss_bytes: int

    def as_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


def benchmark_backend(
    backend: str,
    *,
    size: int,
    samples: int,
    warmup: int,
    repeats: int,
) -> BenchmarkResult:
    trials: list[dict[str, int | float | str]] = []
    for _ in range(repeats):
        command = [
            sys.executable,
            "-m",
            "stream_stats._benchmark_worker",
            "--backend",
            backend,
            "--size",
            str(size),
            "--samples",
            str(samples),
            "--warmup",
            str(warmup),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode:
            detail = completed.stderr.strip().splitlines()[-1] if completed.stderr else "worker failed"
            raise RuntimeError(f"benchmark for '{backend}' failed: {detail}")
        trials.append(json.loads(completed.stdout))

    return BenchmarkResult(
        backend=backend,
        samples=samples,
        repeats=repeats,
        median_elapsed_ns=int(statistics.median(float(t["elapsed_ns"]) for t in trials)),
        median_samples_per_second=statistics.median(
            float(t["samples_per_second"]) for t in trials
        ),
        max_peak_rss_bytes=max(int(t["peak_rss_bytes"]) for t in trials),
    )
