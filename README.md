# stream-stats

`stream-stats` calculates statistics over fixed-size windows of streaming or
iterated numeric data. Applications use one Python API and can select an
implementation backend without changing their calculation code.

Implemented first-party `WindowStatistics` backends are Python, Cython, C,
C++, Rust, Go, Java, and Scala. The Python backend is the portable default,
while NumPy is an intentionally simple baseline. The Cython backend directly
binds the canonical recycled-array C core for low scalar-call overhead.
Assembly was evaluated but is not published because it did not clear the
performance gate. Native and JVM implementations live in [`backends/`](backends/)
and conform to the contract in
[`docs/BACKENDS.md`](docs/BACKENDS.md).

## Quick start

```python
from stream_stats import Window

window = Window(3, statistics=("mean", "min", "max"), backend="python")

for value in [2, 4, 9, 5]:
    print(window.push(value))
```

Or consume any iterable lazily:

```python
results = Window(100, statistics=("mean", "std")).map(values)
for result in results:
    print(result.mean)
```

For exact percentiles and percentile ranks, use the recycled-tree API:

```python
from stream_stats import WindowStatistics

statistics = WindowStatistics(100, backend="rust")
for value in values:
    statistics.add(value)

print(statistics.percentile(95))
print(statistics.percentile_of(42))
```

An intentionally straightforward NumPy implementation is available through
the `baseline` extra as `NumpyWindowStatistics`.

Select another implementation explicitly:

```shell
statistics = WindowStatistics(100, backend="go")
```

Use `list_window_statistics_backends()` to inspect locally built artifacts.
Requesting an unavailable backend raises `BackendUnavailableError`; it never
silently falls back to Python.

## Benchmark CLI

The benchmark runner executes each backend in an isolated process and reports
elapsed time, throughput, and peak resident memory:

```shell
stream-stats benchmark --backend python --size 1024 --samples 100000
stream-stats benchmark --backend all --format json
stream-stats backends
```

Only built/installed backends are benchmarked by default. Requesting a named
backend that is unavailable produces a build/configuration hint.

## Repository layout

```text
stream-stats/
├── pyproject.toml
├── src/stream_stats/       # public Python API, backend registry, CLI
├── tests/                  # API, correctness, and CLI tests
├── docs/                   # API and backend contracts
└── backends/               # assembly, C, C++, Rust, Go, Scala, Java
```

## Development

```shell
python -m pip install -e '.[dev]'
make test
stream-stats benchmark --backend python
PYTHONPATH=src python benchmarks/compare_window_statistics.py
```

The Python comparison benchmark sweeps window sizes `16`, `64`, `256`, `1024`,
`4096`, and `65536` using a deterministic uniform pseudo-random stream. See the
[benchmark methodology](docs/BENCHMARKS.md) for the dataset and workload.

Record per-operation results and generate plots with:

```shell
PYTHONPATH=src python benchmarks/benchmark_operations.py
```

`make build-backends` builds every locally supported implementation and
`make benchmark-report` records the full 100,000-update report.

The Cython backend is optional so installing the base source package does not
require a C compiler:

```shell
python -m pip install -e '.[cython]'
make build-cython
```

See [API documentation](docs/API.md), [backend integration](docs/BACKENDS.md),
and [benchmark methodology](docs/BENCHMARKS.md).
