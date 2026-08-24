"""Compare optimized and NumPy window-statistics implementations.

Run from the repository root with an environment containing the baseline extra:

    PYTHONPATH=src python benchmarks/compare_window_statistics.py
"""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

WINDOW_SIZES = (16, 64, 256, 1_024, 4_096, 65_536)
DATASET_SEED = 0x1234ABCD


def _values(samples: int):
    """Yield a repeatable discrete-uniform stream over the interval [0, 1]."""
    state = DATASET_SEED
    for _ in range(samples):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        yield state / 0xFFFFFFFF


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _worker(
    implementation: str,
    size: int,
    steady_updates: int,
    query_every: int,
) -> dict[str, Any]:
    from stream_stats import NumpyWindowStatistics, WindowStatistics

    implementation_type = {
        "optimized": WindowStatistics,
        "numpy": NumpyWindowStatistics,
    }[implementation]
    total_updates = size + steady_updates
    tracemalloc.start()
    checksum = 0.0
    query_count = 0
    started = time.perf_counter_ns()
    window = implementation_type(size)
    for update_index, value in enumerate(_values(total_updates), start=1):
        window.add(value)
        if update_index % query_every == 0 or update_index == total_updates:
            checksum += window.mean
            checksum += window.variance
            checksum += window.percentile(50)
            query_count += 1
    elapsed_ns = time.perf_counter_ns() - started
    _, python_peak = tracemalloc.get_traced_memory()
    return {
        "implementation": implementation,
        "window_size": size,
        "total_updates": total_updates,
        "steady_updates": steady_updates,
        "query_count": query_count,
        "elapsed_ns": elapsed_ns,
        "updates_per_second": total_updates / (elapsed_ns / 1_000_000_000),
        "python_peak_bytes": python_peak,
        "peak_rss_bytes": _peak_rss_bytes(),
        "checksum": checksum,
    }


def _run_trial(
    implementation: str, size: int, args: argparse.Namespace
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        implementation,
        "--size",
        str(size),
        "--updates",
        str(args.updates),
        "--query-every",
        str(args.query_every),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=list(WINDOW_SIZES))
    parser.add_argument(
        "--updates",
        type=int,
        default=100_000,
        help="steady-state updates after initially filling each window",
    )
    parser.add_argument(
        "--query-every",
        type=int,
        default=256,
        help="calculate mean, variance, and median every N updates",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--worker", choices=("optimized", "numpy"))
    parser.add_argument("--size", type=int, help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if any(size <= 0 for size in args.sizes):
        raise SystemExit("all window sizes must be positive")
    if args.updates < 0 or args.query_every <= 0 or args.repeats <= 0:
        raise SystemExit(
            "updates cannot be negative; query interval and repeats must be positive"
        )
    if args.worker:
        if args.size is None or args.size <= 0:
            raise SystemExit("worker size must be positive")
        result = _worker(args.worker, args.size, args.updates, args.query_every)
        print(json.dumps(result))
        return

    sizes = tuple(dict.fromkeys(args.sizes))
    print(
        f"dataset=LCG(seed=0x{DATASET_SEED:08X}, range=[0,1]) "
        f"steady_updates={args.updates:,} query_every={args.query_every:,} "
        f"repeats={args.repeats}"
    )
    print(
        f"{'WINDOW':>8} {'IMPLEMENTATION':<16} {'UPDATES':>10} "
        f"{'MEDIAN':>11} {'UPDATES/S':>14} "
        f"{'PY PEAK':>11} {'RSS PEAK':>11}"
    )
    for size in sizes:
        for implementation in ("optimized", "numpy"):
            trials = [
                _run_trial(implementation, size, args) for _ in range(args.repeats)
            ]
            elapsed = statistics.median(trial["elapsed_ns"] for trial in trials)
            throughput = statistics.median(
                trial["updates_per_second"] for trial in trials
            )
            python_peak = max(trial["python_peak_bytes"] for trial in trials)
            rss_peak = max(trial["peak_rss_bytes"] for trial in trials)
            total_updates = size + args.updates
            print(
                f"{size:>8,} {implementation:<16} {total_updates:>10,} "
                f"{elapsed / 1e9:>9.4f}s {throughput:>14,.0f} "
                f"{python_peak / 2**20:>8.2f} MiB "
                f"{rss_peak / 2**20:>8.2f} MiB"
            )


if __name__ == "__main__":
    main()
