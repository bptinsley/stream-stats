# Benchmark methodology

`stream-stats benchmark` runs every trial in a fresh child process. This keeps
peak resident-set measurements from accumulating across backends and prevents
one runtime (for example a JVM) from warming another backend's trial.

The input is a deterministic pseudo-random iterator, so input allocation is
not included as a large resident collection. Every sample requests all core
statistics. The runner performs a configurable warm-up, resets the window,
then records wall-clock nanoseconds and process peak RSS. It reports median
elapsed time/throughput and maximum peak RSS across trials.

Peak RSS includes the Python interpreter and language runtime. That is
intentional: it represents the cost users pay to select a backend. It is not a
measurement of only the backend's allocator. Compare results on the same
machine, operating system, Python version, compiler settings, and power mode.

## Optimized Python versus NumPy baseline

`benchmarks/compare_window_statistics.py` compares `WindowStatistics` with
`NumpyWindowStatistics` at these window sizes by default:

```text
16, 64, 256, 1024, 4096, 65536
```

Each size/implementation/repeat runs in a fresh process. A trial constructs the
implementation, adds exactly `window_size` values to fill it, then adds 100,000
more values to exercise steady-state removal and insertion. Consequently, the
dataset contains `window_size + 100,000` values for each window size. The trial
calculates mean, population variance, and the 50th percentile after every 256
updates and after the final update. Construction, input generation, updates,
evictions, and scheduled queries are included in elapsed time.

### Dataset

Values come from a deterministic 32-bit linear congruential generator (LCG):

```text
seed = 0x1234ABCD
state = (1664525 * state + 1013904223) mod 2**32
value = state / (2**32 - 1)
```

This produces a repeatable sequence of `float64`-compatible values distributed
approximately uniformly over `[0, 1]`. Every implementation and repeat starts
from the same seed, and every window size consumes a prefix of the same
sequence. The iterator generates values one at a time, so the benchmark does
not allocate a large input collection or count one as implementation memory.
This synthetic dataset emphasizes general update and order-statistic costs; it
does not model clustered values, many duplicates, sorted input, NaNs, or
application-specific distributions. Those should be separate benchmark cases.

The benchmark reports median elapsed time and throughput, maximum Python-visible
peak allocation from `tracemalloc`, and maximum process peak RSS. NumPy's native
allocations are not fully visible to `tracemalloc`, so RSS is the fairer total
memory comparison.

Customize the sweep and workload with:

```shell
PYTHONPATH=src python benchmarks/compare_window_statistics.py \
  --sizes 16 64 256 1024 4096 65536 \
  --updates 100000 --query-every 256 --repeats 3
```

## Operation-level records and plots

`benchmarks/benchmark_operations.py` discovers built engines and measures
`add`, arbitrary removal plus replacement, oldest removal plus replacement,
every aggregate property, median, percentile-of-value, and bulk addition at all
six window sizes. It writes reproducible metadata and median measurements to
JSON and CSV, then creates a multi-panel logarithmic PNG plot:

```shell
python -m pip install '.[benchmark]'
PYTHONPATH=src python benchmarks/benchmark_operations.py
```

Outputs are stored under [`benchmarks/results/`](../benchmarks/results/).
Constant-time optimized queries run 100,000 iterations by default. Potentially
linear NumPy operations use an adaptive iteration count targeting at least 10
million inspected window values and at least 100 calls. Each recorded row
identifies `scalar_python_facade`, `bulk_python_facade`, or `worker_startup` and
records its batch size and actual iteration count. Java and Scala startup is
reported separately. Their `count` and `sum` properties use adapter-side
caches; the remaining statistics measure worker round trips.

Each implementation/window/repeat runs in a fresh process. Peak RSS includes
the Python facade; for Java and Scala it also adds the live worker RSS sampled
after timed operations. This is a complete runtime footprint sample, not a JVM
heap-only figure or a high-frequency child-process peak trace.

Regenerate the plot from recorded JSON without rerunning measurements:

```shell
PYTHONPATH=src python benchmarks/benchmark_operations.py --plot-only
```

Run one or more selected implementations and replace only their matching rows
in the existing report with:

```shell
PYTHONPATH=src python benchmarks/benchmark_operations.py \
  --implementations cython rust --update-existing
```

Incremental updates require the existing Python/platform identity, update
count, linear-work target, and repeat count to match. Selected window sizes are
replaced while all other backend/size rows are preserved. The JSON metadata
records an incremental-update history.
