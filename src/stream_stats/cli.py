from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .benchmark import BenchmarkResult, benchmark_backend
from .backends import get_backend_info, list_backends


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stream-stats")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("backends", help="show backend availability")

    benchmark = subparsers.add_parser("benchmark", help="compare backend performance")
    benchmark.add_argument("--backend", action="append", default=[])
    benchmark.add_argument("--size", type=_positive, default=1024)
    benchmark.add_argument("--samples", type=_positive, default=100_000)
    benchmark.add_argument("--warmup", type=_positive, default=5_000)
    benchmark.add_argument("--repeats", type=_positive, default=3)
    benchmark.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def _print_backends() -> None:
    print(f"{'BACKEND':<12} {'STATUS':<12} PROVIDER / DETAIL")
    for info in list_backends():
        status = "available" if info.available else "unavailable"
        detail = info.provider if info.available else info.detail
        print(f"{info.name:<12} {status:<12} {detail}")


def _print_results(results: list[BenchmarkResult], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps([result.as_dict() for result in results], indent=2))
        return
    print(f"{'BACKEND':<12} {'MEDIAN':>12} {'SAMPLES/S':>14} {'PEAK RSS':>12}")
    for result in results:
        seconds = result.median_elapsed_ns / 1_000_000_000
        mib = result.max_peak_rss_bytes / (1024 * 1024)
        print(
            f"{result.backend:<12} {seconds:>10.4f}s "
            f"{result.median_samples_per_second:>14,.0f} {mib:>9.2f} MiB"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "backends":
        _print_backends()
        return 0

    requested = args.backend or ["all"]
    if "all" in requested:
        names = [info.name for info in list_backends() if info.available]
    else:
        names = requested
    unavailable = [get_backend_info(name) for name in names if not get_backend_info(name).available]
    if unavailable:
        for info in unavailable:
            print(f"error: backend '{info.name}' is unavailable: {info.detail}", file=sys.stderr)
        return 2

    results = [
        benchmark_backend(
            name,
            size=args.size,
            samples=args.samples,
            warmup=args.warmup,
            repeats=args.repeats,
        )
        for name in names
    ]
    _print_results(results, args.format)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
