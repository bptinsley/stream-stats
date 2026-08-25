"""Record and plot WindowStatistics operation benchmarks."""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timezone
from importlib import metadata as package_metadata
from pathlib import Path
from typing import Any, Callable

WINDOW_SIZES = (16, 64, 256, 1_024, 4_096, 65_536)
DATASET_SEED = 0x1234ABCD
IMPLEMENTATIONS = ("optimized", "numpy")


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("numpy", "matplotlib"):
        try:
            versions[package] = package_metadata.version(package)
        except package_metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _values():
    state = DATASET_SEED
    while True:
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        yield state / 0xFFFFFFFF


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _measure(
    operation: str, iterations: int, callback: Callable[[], float]
) -> dict[str, Any]:
    checksum = 0.0
    started = time.perf_counter_ns()
    for _ in range(iterations):
        checksum += callback()
    elapsed_ns = time.perf_counter_ns() - started
    return {
        "operation": operation,
        "iterations": iterations,
        "elapsed_ns": elapsed_ns,
        "nanoseconds_per_operation": elapsed_ns / iterations,
        "operations_per_second": iterations / (elapsed_ns / 1_000_000_000),
        "checksum": checksum,
    }


def _worker(
    implementation: str,
    size: int,
    update_iterations: int,
    target_linear_items: int,
) -> list[dict[str, Any]]:
    from stream_stats import NumpyWindowStatistics, WindowStatistics

    implementation_type = {
        "optimized": WindowStatistics,
        "numpy": NumpyWindowStatistics,
    }[implementation]
    stream = _values()
    window = implementation_type(size)
    for _ in range(size):
        window.add(next(stream))

    results: list[dict[str, Any]] = []
    results.append(
        _measure("add", update_iterations, lambda: float(window.add(next(stream))))
    )

    mirror = deque(window._values_in_order())

    def remove_value_and_add() -> float:
        removed = mirror[-1]
        window.remove(removed)
        mirror.pop()
        added = next(stream)
        window.add(added)
        mirror.append(added)
        return removed

    removal_iterations = min(
        update_iterations, max(100, target_linear_items // size)
    )
    results.append(
        _measure("remove(value)+add", removal_iterations, remove_value_and_add)
    )

    def remove_oldest_and_add() -> float:
        removed = window.remove_oldest()
        mirror.popleft()
        added = next(stream)
        window.add(added)
        mirror.append(added)
        return removed

    results.append(
        _measure("remove_oldest+add", update_iterations, remove_oldest_and_add)
    )

    linear_iterations = (
        update_iterations
        if implementation == "optimized"
        else min(update_iterations, max(100, target_linear_items // size))
    )
    query_specs: tuple[tuple[str, int, Callable[[], float]], ...] = (
        ("count", update_iterations, lambda: float(window.count)),
        ("sum", linear_iterations, lambda: window.sum),
        ("min", linear_iterations, lambda: window.min),
        ("max", linear_iterations, lambda: window.max),
        ("mean", linear_iterations, lambda: window.mean),
        ("variance", linear_iterations, lambda: window.variance),
        ("std", linear_iterations, lambda: window.std),
        ("percentile(50)", linear_iterations, lambda: window.percentile(50)),
        (
            "percentile_of(0.5)",
            linear_iterations,
            lambda: window.percentile_of(0.5),
        ),
    )
    for operation, iterations, callback in query_specs:
        results.append(_measure(operation, iterations, callback))

    peak_rss = _peak_rss_bytes()
    for result in results:
        result.update(
            {
                "implementation": implementation,
                "window_size": size,
                "peak_rss_bytes": peak_rss,
            }
        )
    return results


def _run_worker(
    implementation: str, size: int, args: argparse.Namespace
) -> list[dict[str, Any]]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        implementation,
        "--size",
        str(size),
        "--updates",
        str(args.updates),
        "--target-linear-items",
        str(args.target_linear_items),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def _aggregate_trials(trials: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for trial in trials:
        for row in trial:
            key = (row["operation"], row["implementation"], row["window_size"])
            grouped.setdefault(key, []).append(row)

    results: list[dict[str, Any]] = []
    for (operation, implementation, size), rows in grouped.items():
        elapsed = statistics.median(row["elapsed_ns"] for row in rows)
        iterations = rows[0]["iterations"]
        results.append(
            {
                "operation": operation,
                "implementation": implementation,
                "window_size": size,
                "iterations": iterations,
                "repeats": len(rows),
                "median_elapsed_ns": elapsed,
                "median_nanoseconds_per_operation": elapsed / iterations,
                "median_operations_per_second": iterations / (elapsed / 1e9),
                "max_peak_rss_bytes": max(row["peak_rss_bytes"] for row in rows),
            }
        )
    return sorted(
        results,
        key=lambda row: (
            row["operation"],
            row["window_size"],
            row["implementation"],
        ),
    )


def _write_results(
    output_directory: Path,
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "package_versions": _package_versions(),
        "window_sizes": args.sizes,
        "steady_update_iterations": args.updates,
        "target_linear_items": args.target_linear_items,
        "repeats": args.repeats,
        "dataset": {
            "generator": "32-bit linear congruential generator",
            "seed": f"0x{DATASET_SEED:08X}",
            "multiplier": 1664525,
            "increment": 1013904223,
            "modulus": 2**32,
            "range": "[0, 1]",
        },
    }
    json_path = output_directory / "window_statistics_operations.json"
    csv_path = output_directory / "window_statistics_operations.csv"
    json_path.write_text(
        json.dumps({"metadata": metadata, "results": rows}, indent=2) + "\n"
    )
    with csv_path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def _plot(output_directory: Path, rows: list[dict[str, Any]]) -> Path:
    matplotlib_cache = output_directory / ".matplotlib"
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache.resolve()))
    os.environ.setdefault("MPLBACKEND", "Agg")
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "plotting requires matplotlib; install stream-stats[benchmark]"
        ) from exc

    operations = list(dict.fromkeys(row["operation"] for row in rows))
    columns = 3
    row_count = (len(operations) + columns - 1) // columns
    figure, axes = plt.subplots(row_count, columns, figsize=(15, 3.8 * row_count))
    flat_axes = list(axes.flat)
    colors = {"optimized": "#1565c0", "numpy": "#ef6c00"}
    for operation, axis in zip(operations, flat_axes):
        for implementation in IMPLEMENTATIONS:
            selected = [
                row
                for row in rows
                if row["operation"] == operation
                and row["implementation"] == implementation
            ]
            axis.plot(
                [row["window_size"] for row in selected],
                [row["median_nanoseconds_per_operation"] for row in selected],
                marker="o",
                label=implementation,
                color=colors[implementation],
            )
        axis.set_title(operation)
        axis.set_xscale("log", base=2)
        axis.set_yscale("log")
        axis.grid(True, which="both", alpha=0.25)
        axis.set_xlabel("window size")
        axis.set_ylabel("nanoseconds / operation")
    for axis in flat_axes[len(operations) :]:
        axis.set_visible(False)
    handles, labels = figure.axes[0].get_legend_handles_labels()
    figure.suptitle(
        "Window statistics operation performance", x=0.02, y=0.992, ha="left"
    )
    figure.legend(
        handles,
        labels,
        loc="upper right",
        bbox_to_anchor=(0.98, 0.992),
        ncol=2,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    plot_path = output_directory / "window_statistics_operations.png"
    figure.savefig(plot_path, dpi=160)
    plt.close(figure)
    return plot_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", nargs="+", type=int, default=list(WINDOW_SIZES))
    parser.add_argument("--updates", type=int, default=100_000)
    parser.add_argument("--target-linear-items", type=int, default=10_000_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/results"))
    parser.add_argument(
        "--plot-only",
        action="store_true",
        help="regenerate the plot from the existing JSON results",
    )
    parser.add_argument("--worker", choices=IMPLEMENTATIONS, help=argparse.SUPPRESS)
    parser.add_argument("--size", type=int, help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if any(size <= 0 for size in args.sizes):
        raise SystemExit("all window sizes must be positive")
    if args.updates <= 0 or args.target_linear_items <= 0 or args.repeats <= 0:
        raise SystemExit("updates, target-linear-items, and repeats must be positive")
    if args.plot_only:
        result_path = args.output_dir / "window_statistics_operations.json"
        if not result_path.exists():
            raise SystemExit(f"recorded results do not exist: {result_path}")
        document = json.loads(result_path.read_text())
        document["metadata"]["package_versions"] = _package_versions()
        result_path.write_text(json.dumps(document, indent=2) + "\n")
        rows = document["results"]
        plot_path = _plot(args.output_dir, rows)
        print(f"wrote {plot_path}")
        return
    if args.worker:
        if args.size is None or args.size <= 0:
            raise SystemExit("worker size must be positive")
        print(
            json.dumps(
                _worker(args.worker, args.size, args.updates, args.target_linear_items)
            )
        )
        return

    trials: list[list[dict[str, Any]]] = []
    for size in tuple(dict.fromkeys(args.sizes)):
        for implementation in IMPLEMENTATIONS:
            print(f"benchmarking {implementation} at window {size:,}...", flush=True)
            for _ in range(args.repeats):
                trials.append(_run_worker(implementation, size, args))
    rows = _aggregate_trials(trials)
    json_path, csv_path = _write_results(args.output_dir, rows, args)
    plot_path = _plot(args.output_dir, rows)
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {plot_path}")


if __name__ == "__main__":
    main()
