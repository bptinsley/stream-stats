from __future__ import annotations

import argparse
import json
import resource
import sys
import time

from . import Window


def _values(samples: int):
    """Deterministic stream without allocating an input collection."""
    state = 0x1234ABCD
    for _ in range(samples):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        yield state / 0xFFFFFFFF


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def run(backend: str, size: int, samples: int, warmup: int) -> dict[str, int | float | str]:
    statistics = ("count", "sum", "min", "max", "mean", "variance", "std")
    window = Window(size, statistics, backend=backend)
    for value in _values(warmup):
        window.push(value)
    window.reset()

    started = time.perf_counter_ns()
    for value in _values(samples):
        window.push(value)
    elapsed_ns = time.perf_counter_ns() - started
    return {
        "backend": backend,
        "elapsed_ns": elapsed_ns,
        "samples": samples,
        "samples_per_second": samples / (elapsed_ns / 1_000_000_000),
        "peak_rss_bytes": _peak_rss_bytes(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--size", required=True, type=int)
    parser.add_argument("--samples", required=True, type=int)
    parser.add_argument("--warmup", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.backend, args.size, args.samples, args.warmup)))


if __name__ == "__main__":
    main()
