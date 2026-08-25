# API reference

## `Window`

```python
Window(
    size: int,
    statistics: Iterable[str] = ("mean",),
    *,
    backend: str | None = None,
)
```

Creates a stateful fixed-size trailing window. `size` must be positive.
Supported statistics are `count`, `sum`, `min`, `max`, `mean`, `variance`,
and `std`. Variance and standard deviation are population statistics.

`backend=None` reads `STREAM_STATS_BACKEND`, defaulting to `python`.

- `push(value)` adds one finite real value and returns a `WindowSnapshot`.
- `map(values)` lazily yields one snapshot per input value.
- `reset()` empties the window while preserving its configuration.

## `WindowSnapshot`

An immutable mapping with a `count` entry and the requested statistics. Values
are also available as attributes (`snapshot.mean`) and through `as_dict()`.

## Backend discovery

- `list_backends()` returns availability metadata for all first-party and
  installed plugin backends.
- `get_backend_info(name)` inspects one backend.
- `register_backend(name, factory)` registers a backend for the current process.

Third-party distributions can register a factory through the
`stream_stats.backends` Python entry-point group.

## `WindowStatistics`

```python
WindowStatistics(window_size: int, *, backend: str = "python")
```

Maintains exact statistics over a fixed-size window using a recycled,
index-addressed AVL multiset and cyclic arrival buffer.

- `add(value)` adds a finite real value and returns the automatically evicted
  oldest value, or `None` before the window becomes full.
- `add_many(values)` validates the complete batch, adds it, and returns one
  optional eviction result per value. The Cython backend has a zero-copy input
  path for contiguous native-endian double buffers such as `array('d')`,
  compatible `memoryview` objects, and contiguous NumPy `float64` arrays.
- `remove_oldest()` removes and returns the oldest value.
- `remove(value)` removes the oldest matching occurrence.
- `clear()` empties the window while retaining allocated storage.
- `snapshot()` returns all constant-time aggregates in one immutable value and
  is valid for an empty window.
- `close()` deterministically releases native handles or worker processes;
  engines also support `with` and expose `closed`.
- `percentile(p)` returns the linear-interpolated percentile for `p` in
  `[0, 100]`.
- `percentile_of(value)` returns the duplicate-aware midrank percentage.

Properties are `count`, `sum`, `min`, `max`, `mean`, `variance`, `std`, and
`standard_deviation`. Variance and standard deviation are population
statistics. Empty-window statistics raise `statistics.StatisticsError`, except
`count` and `sum`, which return zero.

Backend names are `python`, `cython`, `c`, `cpp`, `rust`, `go`, `java`, and
`scala` when their local artifacts are built. `cython` is a compiled direct
binding to the canonical C recycled-array core. `assembly` is reserved but
unavailable until an optimized kernel clears its publication gate.

## `NumpyWindowStatistics`

The optional NumPy baseline has the same public behavior but copies its window
array during mutation and recalculates statistics on demand. Install it with:

```shell
python -m pip install 'stream-stats[baseline]'
```

It is intended for correctness and performance comparisons, not as an
automatic fallback.
