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
BUILTIN_IMPLEMENTATIONS = ("optimized", "numpy")


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("numpy", "matplotlib", "Cython"):
        try:
            versions[package] = package_metadata.version(package)
        except package_metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "not available"
    output = (completed.stdout + completed.stderr).strip().splitlines()
    return output[0] if output else "unknown"


def _toolchain_metadata() -> dict[str, str]:
    java = "/opt/homebrew/opt/openjdk/bin/java"
    cython_version = _package_versions().get("Cython", "not installed")
    return {
        "c": _command_version(["cc", "--version"]),
        "cpp": _command_version(["c++", "--version"]),
        "rust": _command_version(["rustc", "--version"]),
        "go": _command_version(["go", "version"]),
        "java": _command_version([java, "-version"]),
        "scala": _command_version(["scalac", "-version"]),
        "cython": f"Cython {cython_version}",
    }


def _available_implementations() -> tuple[str, ...]:
    from stream_stats import list_window_statistics_backends

    foreign = [
        info.name
        for info in list_window_statistics_backends()
        if info.available and info.name != "python"
    ]
    return (*BUILTIN_IMPLEMENTATIONS, *foreign)


def _values():
    state = DATASET_SEED
    while True:
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        yield state / 0xFFFFFFFF


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _complete_process_rss_bytes(window: Any) -> int:
    """Include a live external worker in the facade process memory figure."""
    total = _peak_rss_bytes()
    engine = getattr(window, "_engine", None)
    process = getattr(engine, "_process", None)
    if process is None or process.poll() is not None:
        return total
    try:
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(process.pid)],
            check=True,
            capture_output=True,
            text=True,
        )
        total += int(completed.stdout.strip()) * 1024
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    return total


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

    construction_started = time.perf_counter_ns()
    if implementation == "numpy":
        window = NumpyWindowStatistics(size)
    else:
        backend = "python" if implementation == "optimized" else implementation
        window = WindowStatistics(size, backend=backend)
    construction_ns = time.perf_counter_ns() - construction_started
    stream = _values()
    mirror: deque[float] = deque(maxlen=size)
    for _ in range(size):
        value = next(stream)
        window.add(value)
        mirror.append(value)

    results: list[dict[str, Any]] = []
    if implementation in {"java", "scala"}:
        results.append(
            {
                "operation": "worker_cold_start",
                "iterations": 1,
                "elapsed_ns": construction_ns,
                "nanoseconds_per_operation": float(construction_ns),
                "operations_per_second": 1_000_000_000 / construction_ns,
                "checksum": 0.0,
                "layer": "worker_startup",
                "batch_size": 1,
            }
        )

    def add() -> float:
        value = next(stream)
        evicted = window.add(value)
        mirror.append(value)
        return 0.0 if evicted is None else evicted

    results.append(
        _measure("add", update_iterations, add)
    )

    def remove_value_and_add() -> float:
        removed = mirror[-1]
        window.remove(removed)
        mirror.pop()
        added = next(stream)
        window.add(added)
        mirror.append(added)
        return removed

    removal_iterations = min(update_iterations, max(100, target_linear_items // size))
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
        if implementation != "numpy"
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

    batch_size = min(256, update_iterations)
    batch = tuple(next(stream) for _ in range(batch_size))
    bulk_iterations = max(1, update_iterations // batch_size)

    def add_many() -> float:
        evictions = window.add_many(batch)
        return sum(value for value in evictions if value is not None)

    bulk_result = _measure("add_many/item", bulk_iterations, add_many)
    bulk_result["nanoseconds_per_operation"] /= batch_size
    bulk_result["operations_per_second"] *= batch_size
    bulk_result["layer"] = "bulk_python_facade"
    bulk_result["batch_size"] = batch_size
    results.append(bulk_result)

    peak_rss = _complete_process_rss_bytes(window)
    for result in results:
        result.setdefault("layer", "scalar_python_facade")
        result.setdefault("batch_size", 1)
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
                "median_nanoseconds_per_operation": statistics.median(
                    row["nanoseconds_per_operation"] for row in rows
                ),
                "median_operations_per_second": statistics.median(
                    row["operations_per_second"] for row in rows
                ),
                "max_peak_rss_bytes": max(row["peak_rss_bytes"] for row in rows),
                "layer": rows[0]["layer"],
                "batch_size": rows[0]["batch_size"],
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
    *,
    existing_metadata: dict[str, Any] | None = None,
    updated_implementations: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    fresh_metadata = {
        "recorded_at": now,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "package_versions": _package_versions(),
        "toolchains": _toolchain_metadata(),
        "strict_build_settings": {
            "c": "-O3 -fno-fast-math -ffp-contract=off",
            "cpp": "Release -O3 -fno-fast-math -ffp-contract=off",
            "rust": "cargo build --release; no fast-math intrinsics",
            "go": "go build -buildmode=c-shared; default IEEE arithmetic",
            "java": "OpenJDK always-strict arithmetic",
            "scala": "Scala 3 on OpenJDK always-strict arithmetic",
            "cython": (
                "Cython extension plus canonical C core; -O3 -fno-fast-math "
                "-ffp-contract=off"
            ),
        },
        "window_sizes": args.sizes,
        "steady_update_iterations": args.updates,
        "target_linear_items": args.target_linear_items,
        "repeats": args.repeats,
        "benchmark_layers": [
            "scalar_python_facade",
            "bulk_python_facade",
            "worker_startup",
        ],
        "jvm_memory_method": (
            "Python peak RSS plus live worker RSS sampled after timed operations"
        ),
        "dataset": {
            "generator": "32-bit linear congruential generator",
            "seed": f"0x{DATASET_SEED:08X}",
            "multiplier": 1664525,
            "increment": 1013904223,
            "modulus": 2**32,
            "range": "[0, 1]",
        },
    }
    if existing_metadata is None:
        metadata = fresh_metadata
    else:
        metadata = dict(existing_metadata)
        metadata["last_updated_at"] = now
        metadata["package_versions"] = fresh_metadata["package_versions"]
        metadata["toolchains"] = fresh_metadata["toolchains"]
        metadata["strict_build_settings"] = fresh_metadata[
            "strict_build_settings"
        ]
        metadata["window_sizes"] = sorted(
            set(metadata.get("window_sizes", ())) | set(args.sizes)
        )
        history = list(metadata.get("incremental_updates", ()))
        history.append(
            {
                "recorded_at": now,
                "implementations": list(updated_implementations),
                "window_sizes": list(args.sizes),
                "updates": args.updates,
                "repeats": args.repeats,
            }
        )
        metadata["incremental_updates"] = history
    json_path = output_directory / "window_statistics_operations.json"
    csv_path = output_directory / "window_statistics_operations.csv"
    json_path.write_text(
        json.dumps({"metadata": metadata, "results": rows}, indent=2) + "\n"
    )
    with csv_path.open("w", newline="") as output:
        writer = csv.DictWriter(
            output, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def _plot(output_directory: Path, rows: list[dict[str, Any]]) -> Path:
    matplotlib_cache = output_directory / ".matplotlib"
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache.resolve()))
    os.environ.setdefault("XDG_CACHE_HOME", str(matplotlib_cache.resolve()))
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
    implementations = list(
        dict.fromkeys(row["implementation"] for row in rows)
    )
    color_map = plt.get_cmap("tab20")
    colors = {
        implementation: color_map(index % color_map.N)
        for index, implementation in enumerate(implementations)
    }
    for operation, axis in zip(operations, flat_axes):
        for implementation in implementations:
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
    parser.add_argument("--implementations", nargs="+")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help=(
            "replace matching implementation/window rows in existing JSON/CSV "
            "while preserving all other recorded results"
        ),
    )
    parser.add_argument("--worker", help=argparse.SUPPRESS)
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

    available = _available_implementations()
    implementations = tuple(dict.fromkeys(args.implementations or available))
    unavailable = sorted(set(implementations) - set(available))
    if unavailable:
        raise SystemExit("unavailable implementations: " + ", ".join(unavailable))
    existing_metadata: dict[str, Any] | None = None
    existing_rows: list[dict[str, Any]] = []
    if args.update_existing:
        existing_path = args.output_dir / "window_statistics_operations.json"
        if not existing_path.exists():
            raise SystemExit(f"recorded results do not exist: {existing_path}")
        existing_document = json.loads(existing_path.read_text())
        existing_metadata = existing_document["metadata"]
        expected = {
            "steady_update_iterations": args.updates,
            "target_linear_items": args.target_linear_items,
            "repeats": args.repeats,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        }
        mismatches = [
            key
            for key, value in expected.items()
            if existing_metadata.get(key) != value
        ]
        if mismatches:
            raise SystemExit(
                "incremental update is incompatible with existing metadata: "
                + ", ".join(mismatches)
            )
        existing_rows = existing_document["results"]

    trials: list[list[dict[str, Any]]] = []
    for size in tuple(dict.fromkeys(args.sizes)):
        for implementation in implementations:
            print(f"benchmarking {implementation} at window {size:,}...", flush=True)
            for _ in range(args.repeats):
                trials.append(_run_worker(implementation, size, args))
    rows = _aggregate_trials(trials)
    if existing_rows:
        replaced = {
            (implementation, size)
            for implementation in implementations
            for size in args.sizes
        }
        rows = [
            row
            for row in existing_rows
            if (row["implementation"], row["window_size"]) not in replaced
        ] + rows
        rows.sort(
            key=lambda row: (
                row["operation"],
                row["window_size"],
                row["implementation"],
            )
        )
    json_path, csv_path = _write_results(
        args.output_dir,
        rows,
        args,
        existing_metadata=existing_metadata,
        updated_implementations=implementations,
    )
    plot_path = _plot(args.output_dir, rows)
    print(f"wrote {json_path}")
    print(f"wrote {csv_path}")
    print(f"wrote {plot_path}")


if __name__ == "__main__":
    main()
