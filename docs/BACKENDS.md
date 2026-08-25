# Backend contract

Every backend must preserve the Python API's observable behavior. Given a
window size and ordered statistic names, its factory returns an isolated,
stateful object with:

```python
name: str
push(value: float) -> Mapping[str, float | int]
reset() -> None
```

The result of `push` contains each requested statistic. A backend may include
`count`; otherwise the Python facade supplies it. Inputs have already been
converted to finite IEEE-754 doubles. Population variance is defined as
`sum((x - mean) ** 2) / count`.

Python plugins expose a factory using this entry point:

```toml
[project.entry-points."stream_stats.backends"]
rust = "stream_stats_rust:create_backend"
```

## Language integration

| Workspace | Intended bridge | Artifact |
|---|---|---|
| `assembly` | C ABI loaded by a thin Python extension | shared library |
| `c` | CPython limited API or CFFI | extension/shared library |
| `cpp` | pybind11 | extension module |
| `rust` | PyO3/maturin | extension module/wheel |
| `go` | cgo `c-shared` plus Python adapter | shared library |
| `java` | persistent binary/JSON-lines worker | JAR |
| `scala` | persistent binary/JSON-lines worker | JAR |

Each adapter must release the GIL while performing backend-only batch work
where its bridge permits it. It must not silently fall back to Python: an
unavailable compiled artifact is an explicit configuration error.

## Correctness gate

Before inclusion in comparative benchmarks, every implementation must run the
shared conformance vectors against the Python backend. Exact integers and
`count` must match; floating-point statistics use a documented relative and
absolute tolerance.
