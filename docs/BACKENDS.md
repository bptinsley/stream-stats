# Window statistics backend contract

The public `WindowStatistics` facade always returns the same Python type and
delegates to an engine selected with `backend=`. Engines implement scalar and
bulk mutation, removal, clear, one-call snapshot, exact percentiles and
percentile rank, deterministic close, and context-manager cleanup. Inputs are
finite normalized binary64 values; duplicate values use midrank semantics.

Plugins register factories in `stream_stats.window_statistics`. Local builds
are discovered directly from their standard workspace artifact paths. Missing
artifacts raise `BackendUnavailableError` and never fall back to Python.

| Backend | Storage | Python bridge | Local artifact |
|---|---|---|---|
| `python` | recycled Python parallel arrays | direct | built in |
| `cython` | canonical recycled C arrays | direct Cython extension | extension module |
| `numpy` | copied NumPy array baseline | explicitly named class | optional dependency |
| `c` | recycled C arrays | stable C ABI via `ctypes` | shared library |
| `cpp` | pre-sized `std::vector` arrays | stable C ABI via `ctypes` | shared library |
| `rust` | pre-sized safe `Vec` arrays | stable C ABI via `ctypes` | shared library |
| `go` | pre-sized primitive slices | c-shared ABI and `cgo.Handle` | shared library |
| `java` | primitive JVM arrays | framed persistent worker | runnable JAR |
| `scala` | primitive JVM arrays | same framing, independent core | class artifact |
| `assembly` | performance-gated C/assembly hybrid | not published | none |

Native libraries implement ABI version 1 from
`backends/c/include/stream_stats_window.h`. The Go library exposes compatible
symbols but stores only opaque integer handles across cgo. Native adapters own
exactly one handle, reject inherited post-fork use, and close idempotently.
ABI version 1 also provides narrow `count`, `sum`, `min`, and `max` getters so
an adapter does not have to construct a complete snapshot for one scalar.
The Python adapters maintain synchronized `count` and `sum` caches after each
successful mutation; Java and Scala therefore answer those properties without
a worker round trip.
The Cython extension compiles the same C source directly into its extension,
removing `ctypes` dispatch and Python snapshot-object overhead from scalar
properties while retaining the canonical algorithm and conformance behavior.
Its bulk path reuses grow-only input, eviction, and flag buffers and consumes
contiguous native-endian double buffers through the Python buffer protocol
without copying or per-item Python conversion.

Java and Scala use a fixed little-endian, length-prefixed protocol. Every frame
has a request ID and status. The Python owner enforces startup, request, and
shutdown timeouts; rejects malformed, mismatched, truncated, or EOF responses;
does not replay mutations; and terminates failed workers.

## Build and test

From the repository root:

```sh
make build-backends
make test-backends
.venv/bin/python -m pytest
```

The aggregate build currently targets macOS arm64/Homebrew defaults. Individual
workspace README files describe their direct commands. Generated artifacts
remain untracked. Run `list_window_statistics_backends()` after building to
confirm discovery.

## Correctness gate

`conformance/window_statistics.json` is language-neutral and uses hexadecimal
binary64 inputs with independently recorded decimal expectations. Every
available engine is parametrized through the same Python conformance test.
Native tests additionally exercise each language core and recycled storage.
Counts, errors, and eviction order match exactly; documented tolerances apply
to floating-point reassociation differences.
