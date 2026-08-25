# Plan V2: `WindowStatistics` backends in other languages

## Goal

Implement the optimized `WindowStatistics` algorithm in assembly, C, C++,
Rust, Go, Java, and Scala while preserving the Python implementation as the
behavioral reference.

Every implementation will retain all values in a fixed-size window and use:

1. A bounded AVL multiset augmented with subtree aggregates and occurrence
   counts.
2. A fixed-capacity cyclic array that preserves arrival order and identifies
   the value to evict.
3. Recycled node slots so steady-state insertion and deletion do not allocate
   tree nodes.

The foreign implementations must expose the same observable operations,
exceptions, percentile definitions, and asymptotic complexity as the Python
class. Language-specific representations and integration layers may differ.

## Public Python integration

Preserve the existing concrete `WindowStatistics` behavior while introducing
backend delegation in a compatibility-focused change. First move the current
implementation behind a private `_PythonWindowStatisticsEngine` without
changing public results, signatures, exceptions, or lifecycle. Do not require
users to import engine classes directly. Only after compatibility tests pass,
evolve the public constructor as follows:

```python
WindowStatistics(
    window_size: int,
    *,
    backend: str = "python",
) -> WindowStatistics
```

Examples:

```python
python_stats = WindowStatistics(1024)
rust_stats = WindowStatistics(1024, backend="rust")
c_stats = WindowStatistics(1024, backend="c")
```

Keep `NumpyWindowStatistics` as an explicitly named baseline rather than a
silent fallback. A requested backend that is not built or installed must raise
`BackendUnavailableError` with an actionable installation/build message.

The facade delegates to a new internal `WindowStatisticsEngine` protocol:

```python
class WindowStatisticsEngine(Protocol):
    name: str
    window_size: int

    def add(self, value: float) -> float | None: ...
    def add_many(self, values: Iterable[float]) -> list[float | None]: ...
    def remove_oldest(self) -> float: ...
    def remove(self, value: float) -> None: ...
    def clear(self) -> None: ...
    def snapshot(self) -> WindowStatisticsSnapshot: ...
    def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...

    @property
    def count(self) -> int: ...
    @property
    def sum(self) -> float: ...
    @property
    def min(self) -> float: ...
    @property
    def max(self) -> float: ...
    @property
    def mean(self) -> float: ...
    @property
    def variance(self) -> float: ...
    @property
    def std(self) -> float: ...

    def percentile(self, percentile: float) -> float: ...
    def percentile_of(self, value: float) -> float: ...
```

`WindowStatisticsSnapshot` is an immutable Python value containing count, sum,
and optional min, max, mean, variance, and std. `snapshot()` is valid on an
empty engine: count and sum are zero and the remaining fields are `None`.
`add_many()` returns one optional eviction result per input after validating the
entire input batch. The facade exposes idempotent `close()`, `closed`, and
context-manager methods even when the pure-Python engine has no external
resource.

Register engine factories through a dedicated entry-point group so the richer
contract is not confused with the existing push-only backend contract:

```toml
[project.entry-points."stream_stats.window_statistics"]
rust = "stream_stats_rust:create_window_statistics"
```

The existing `stream_stats.backends` entry points may wrap these engines for
the older `Window.push()` API.

Compatibility requirements:

- `WindowStatistics(window_size)` continues to select Python and behave exactly
  as it does today.
- Existing public attributes and documented methods remain available.
- Private diagnostic attributes are not part of the compatibility contract,
  but the private `_validate()` development hook remains accessible for the
  Python engine's tests.
- Backend selection changes delegation, not the public Python type returned to
  callers.
- Add facade/engine tests before moving any tree code so regressions can be
  attributed to integration rather than algorithm changes.
- External engine wrappers record the process ID that created their native
  handle or worker. An operation in a forked child detects the PID change and
  raises `RuntimeError`; callers must explicitly construct new child-process
  engines. Do not reuse inherited Go handles, native locks, or JVM pipes.

## Frozen cross-language semantics

All implementations must agree on the following rules before optimization:

- Window size is a positive integer representable by the implementation's
  index type.
- Inputs are converted by the Python facade to finite IEEE-754 binary64 values.
- Booleans, NaN, and positive or negative infinity are rejected.
- Normalize negative zero to positive zero before storage so ordering and
  returned signs are consistent across languages. Apply the same normalization
  to the Python reference before enabling foreign backends.
- Duplicate values occupy one AVL node with a multiplicity count.
- `add` returns the evicted oldest value or `None` before the window is full.
- `remove(value)` removes the oldest matching occurrence from arrival order.
- Empty `count` and `sum` return `0` and `0.0`; other queries raise the Python
  equivalent of `statistics.StatisticsError`.
- Variance and standard deviation are population statistics.
- `standard_deviation` is a Python alias of `std`.
- `percentile(p)` uses linear interpolation at rank
  `(p / 100) * (count - 1)`.
- `percentile_of(value)` returns duplicate-aware midrank:
  `100 * (less + 0.5 * equal) / count`.
- Counts, eviction order, and errors must match exactly. Floating-point results
  use documented absolute and relative tolerances because tree rotations and
  compilers may associate arithmetic differently.

Freeze binary64 overflow behavior with conformance vectors before implementing
foreign backends:

- Finite inputs remain valid even when sum, M2, variance, standard deviation,
  or interpolation exceeds the finite binary64 range.
- A mathematically overflowing non-negative aggregate returns positive
  infinity rather than raising a language-specific arithmetic exception.
- Internal NaN is permitted only when the mathematical result is undefined by
  the frozen policy; otherwise it is an invariant failure. In particular,
  intermediate `+infinity + -infinity` must not silently corrupt a finite
  expected sum or mean.
- A tiny negative variance within the documented rounding tolerance is clamped
  to positive zero. A materially negative variance is an internal error.
- Strict-conformance builds disable contraction/FMA and other reassociation
  flags. Optimized FMA builds are allowed only after they pass the same
  tolerance vectors and are identified in benchmark metadata.

### Strict arithmetic build settings

Record exact compiler versions and verify emitted code in release CI:

- C/C++ with GCC or Clang: `-fno-fast-math -ffp-contract=off`; MSVC:
  `/fp:strict`.
- Rust: do not enable fast-math intrinsics, disable target FMA for strict builds
  where supported, and inspect generated code because stable compiler controls
  differ by target and release.
- Go: use default IEEE arithmetic without architecture-specific assembly/FMA in
  the strict backend; validate each supported Go release through fixtures.
- Java and Scala: require a JVM with always-strict floating-point semantics and
  use the same specified `Math` operations in both implementations.
- Assembly: provide explicit non-FMA strict kernels; FMA kernels have a distinct
  capability/build label.

Build flags alone are not proof of conformance. The checked-in overflow and
rounding vectors are authoritative.

## Shared data layout

Use structure-of-arrays storage for the canonical native design. Allocate each
array to `window_size` during construction:

```text
keys              float64[window_size]
multiplicities    unsigned index[window_size]
left              signed index[window_size]
right             signed index[window_size]
heights           unsigned small integer[window_size]
subtree_counts    unsigned index[window_size]
subtree_sums      float64[window_size]
subtree_means     float64[window_size]
subtree_m2        float64[window_size]
subtree_mins      float64[window_size]
subtree_maxes     float64[window_size]
free_indexes      index[window_size]
ring              float64[window_size]
```

State also includes root index, free-stack length, distinct count, cyclic head,
and current length. Use a language-appropriate null index (`-1` or a maximum
unsigned sentinel). Validate that `window_size` fits before allocation.

Every backend computes required array lengths and byte sizes with checked
multiplication and addition before allocating. Index-range overflow is an
invalid window size; a valid representable request that the runtime cannot
allocate becomes `MemoryError`. Where practical, include estimated required
bytes in diagnostic text without making message wording part of conformance.
Construction must not publish a partially initialized engine.

Recompute centered moments after insertions, deletions, and rotations using the
parallel merge equations from `plan_1.md`. Do not use the cancellation-prone
`sum_of_squares / count - mean**2` formula. Maintain sum independently and use
the most accurate practical three-term summation supplied by each runtime.

The core operations retain the same complexity in every language:

| Operation | Required complexity |
|---|---:|
| Add with optional eviction | `O(log w)` |
| Remove oldest | `O(log w)` |
| Remove arbitrary value | `O(w + log w)`, simplified to `O(w)` |
| Clear | `O(w)` |
| Count, sum, min, max, mean, variance, std | `O(1)` |
| Percentile | `O(log w)` |
| Percentile of value | `O(log w)` |
| Retained memory | `O(w)` |

Arbitrary `remove(value)` is intentionally the remaining `O(w)` operation and
the current Python benchmark shows that its cyclic-array scan dominates large
windows. V2 accepts and documents this cost to keep ordinary eviction simple.
If real workloads require faster arbitrary removal, evaluate a separate V3
design using monotonic sequence numbers plus per-value occurrence-position
indexes or an order-maintenance structure. Do not complicate every V2 update or
change complexity claims without dedicated memory and throughput evidence.

## Stable native C ABI

Define the C backend first and use its ABI for C, assembly, and Go adapters.
C++ and Rust may also expose compatibility builds even when their preferred
Python bindings are more direct.

```c
typedef struct stream_stats_ws_state stream_stats_ws_state;

typedef int32_t stream_stats_ws_status;

#define STREAM_STATS_WS_OK ((stream_stats_ws_status)0)
#define STREAM_STATS_WS_INVALID_ARGUMENT ((stream_stats_ws_status)1)
#define STREAM_STATS_WS_EMPTY_WINDOW ((stream_stats_ws_status)2)
#define STREAM_STATS_WS_VALUE_NOT_FOUND ((stream_stats_ws_status)3)
#define STREAM_STATS_WS_OUT_OF_MEMORY ((stream_stats_ws_status)4)
#define STREAM_STATS_WS_INTERNAL_ERROR ((stream_stats_ws_status)5)
#define STREAM_STATS_WS_CLOSED ((stream_stats_ws_status)6)

typedef struct {
    uint32_t abi_version;
    uint32_t struct_size;
    uint64_t count;
    double sum;
    double min;
    double max;
    double mean;
    double variance;
    double std;
    uint8_t has_values;
    uint8_t reserved[7];
} stream_stats_ws_snapshot;

uint32_t stream_stats_ws_abi_version(void);
const char *stream_stats_ws_last_error(void);

stream_stats_ws_status stream_stats_ws_create(
    uint64_t window_size,
    stream_stats_ws_state **out
);
void stream_stats_ws_destroy(stream_stats_ws_state *state);
stream_stats_ws_status stream_stats_ws_clear(stream_stats_ws_state *state);

stream_stats_ws_status stream_stats_ws_add(
    stream_stats_ws_state *state,
    double value,
    uint8_t *did_evict,
    double *evicted
);
stream_stats_ws_status stream_stats_ws_remove_oldest(
    stream_stats_ws_state *state,
    double *removed
);
stream_stats_ws_status stream_stats_ws_remove_value(
    stream_stats_ws_state *state,
    double value
);

stream_stats_ws_status stream_stats_ws_snapshot_get(
    const stream_stats_ws_state *state,
    stream_stats_ws_snapshot *out
);
stream_stats_ws_status stream_stats_ws_percentile(
    const stream_stats_ws_state *state,
    double percentile,
    double *out
);
stream_stats_ws_status stream_stats_ws_percentile_of(
    const stream_stats_ws_state *state,
    double value,
    double *out
);
```

`stream_stats_ws_snapshot_get` is the preferred aggregate query. It returns all
constant-time values in one versioned structure and avoids seven Python-to-FFI
transitions. It succeeds for an empty window with `count = 0`, `sum = 0.0`, and
`has_values = 0`; consumers must not read the other numeric fields in that
case. Individual Python properties preserve their empty-window behavior.

The snapshot ABI is naturally aligned and must never use compiler packing.
Callers initialize `abi_version` and `struct_size = sizeof(their_structure)`
before the call. The library rejects incompatible major versions, writes only
fields fully contained by the supplied size, and leaves unknown trailing caller
storage unchanged. Every native build uses compile-time size, alignment, and
field-offset assertions; ABI tests compare those values across the published
compiler matrix. Reserved bytes are written as zero.

Add a bulk function before performance comparisons so bridge overhead can be
measured separately from algorithm cost. Optional caller-allocated arrays have
one slot per input and may be null when eviction details are not required:

```c
stream_stats_ws_status stream_stats_ws_add_many(
    stream_stats_ws_state *state,
    const double *values,
    uint64_t length,
    uint8_t *did_evict,
    double *evicted,
    uint64_t eviction_capacity,
    stream_stats_ws_snapshot *final_snapshot
);
```

For every input `i`, `did_evict[i]` indicates whether `evicted[i]` is valid.
When eviction outputs are requested, both output arrays must have at least
`length` elements and `eviction_capacity >= length`. To omit per-input eviction
details, pass both pointers as null and capacity zero; mixed null/non-null
outputs are invalid.
`final_snapshot` returns the state after the full batch. Mutation stops at the
first internal error. Both the adapter and native function validate the entire
input array before mutation, so an invalid value cannot create a partial batch.
A future ABI revision may add `items_consumed` and explicitly documented
partial-commit semantics if streaming directly from unvalidated memory becomes
necessary.

Requirements:

- Opaque state ownership; exactly one successful `destroy` per `create`.
- No exceptions or panics cross the ABI.
- Namespaced symbols, fixed-width public types, `uint8_t` flags instead of C
  `bool`, fixed-width `int32_t` status codes instead of ABI-visible enums, and
  explicit symbol visibility.
- Versioned snapshot structures with `struct_size` for forwards-compatible
  extension.
- Versioned symbols or an ABI version function.
- Thread-local diagnostic text for unexpected failures, while status codes
  remain authoritative.
- `stream_stats_ws_last_error()` remains valid until the next ABI call on the
  same thread and must never be used as machine-readable error state.
- Scalar functions for API completeness and bulk functions for fair throughput
  measurement.
- Sanitizer-clean builds and allocator-failure tests.

## C implementation

Workspace: `backends/c/`

Use the shared structure-of-arrays representation in one opaque allocation
owner. The simplest safe first version allocates each array separately with
checked multiplication; a later version may benchmark one aligned allocation
with calculated offsets.

Implementation details:

- Use `uint32_t` indexes when the requested size fits; reject larger windows or
  build a documented 64-bit-index variant.
- Implement AVL traversal iteratively with a preallocated ancestor/direction
  scratch path sized from the maximum AVL height for the configured capacity.
- Store multiplicities and subtree counts in at least 64 bits.
- Disable FMA contraction and reassociation in strict-conformance builds. A
  separately labeled optimized build may use `fma` after tolerance validation
  and profiling demonstrate a benefit.
- Commit cyclic-buffer changes only after tree mutation succeeds.
- Provide an internal `stream_stats_ws_validate` function in debug/test builds.
- Build shared and static libraries with CMake; test with AddressSanitizer,
  UndefinedBehaviorSanitizer, and Valgrind where available.

Python bridge:

- Start with CPython's limited C API for stable-ABI wheels, or CFFI during
  early development if it materially shortens iteration time.
- Convert status codes to the exact Python exception classes.
- Release the GIL around bulk operations, not around tiny scalar queries where
  transition cost would dominate.
- Publish wheels for supported CPython versions and platforms.

## Assembly implementation

Workspace: `backends/assembly/`

Treat this explicitly as a C/assembly hybrid, not an independent all-assembly
class. Do not duplicate allocation, error handling, or Python integration in
assembly. Build a separately selectable `assembly` backend from the C control
layer plus architecture-specific implementations of profiled hot kernels. Name
artifacts and benchmark labels `c-assembly` internally even if the user-facing
selector remains `assembly`.

Initial targets:

- AArch64 for Apple Silicon and Linux arm64.
- x86-64 System V for Linux/macOS where applicable.
- x86-64 Microsoft ABI only after the first two targets pass conformance.

Candidate assembly kernels:

- Subtree metadata recomputation.
- Centered-moment merge.
- Rank-selection traversal.
- Ring compaction used by arbitrary removal.
- Batched insertion input validation and normalization.

AVL rotations and pointer/index relinking may remain in C until profiling shows
a benefit. Tree operations are branch-heavy and pointer-chasing, so hand-written
assembly is not automatically faster. Require an end-to-end improvement over
optimized C before retaining a kernel.

The publication gate is a statistically repeatable improvement on stable
hardware for at least one representative update-heavy workload, with no
material regression across the other required window sizes. If no kernel meets
that gate, retain compiler-generated C and report that assembly was evaluated
rather than publishing a misleading separate backend.

Build and safety requirements:

- Preserve the platform ABI, stack alignment, callee-saved registers, unwind
  information where required, and non-executable stack markers.
- Provide C reference functions selected on unsupported architectures.
- Differential-test every assembly kernel against the C implementation with
  randomized states before full-window testing.
- Inspect generated compiler assembly before deciding to maintain handwritten
  code.

## C++ implementation

Workspace: `backends/cpp/`

Implement a `WindowStatistics` class using RAII-owned, pre-sized `std::vector`
arrays. Avoid node-level `new`/`delete` and avoid boxed polymorphic nodes.

Recommended interface:

```cpp
class WindowStatistics final {
public:
    explicit WindowStatistics(std::size_t window_size);
    std::optional<double> add(double value);
    double remove_oldest();
    void remove(double value);
    void clear() noexcept;
    // Aggregate and percentile accessors.
};
```

Use a small internal exception hierarchy, catch every exception at the Python
boundary, and translate it to the canonical Python error. Mark moves and clear
operations `noexcept` where truthful. Disable copying unless a deep-copy API is
explicitly required.

Expose the class with pybind11:

- Publish a factory under backend name `cpp`.
- Use `py::gil_scoped_release` for bulk additions and long removal batches.
- Return Python `None` or a float from `add`.
- Build with CMake and scikit-build-core.
- Test standard-library variants and sanitizer builds.

## Rust implementation

Workspace: `backends/rust/`

Implement the core as a safe Rust crate with pre-sized `Vec<T>` arrays and a
sentinel index. Keep unsafe code out of the algorithm. If an unsafe optimization
is later justified, isolate it in a small module with explicit safety
invariants and Miri tests.

Recommended core API:

```rust
pub struct WindowStatistics { /* private arrays and state */ }

impl WindowStatistics {
    pub fn new(window_size: usize) -> Result<Self, WindowError>;
    pub fn add(&mut self, value: f64) -> Result<Option<f64>, WindowError>;
    pub fn remove_oldest(&mut self) -> Result<f64, WindowError>;
    pub fn remove(&mut self, value: f64) -> Result<(), WindowError>;
    // Queries and clear.
}
```

Use PyO3 and maturin for Python wheels. Convert `WindowError` variants directly
to canonical Python exceptions. A panic must never cross the FFI boundary;
ordinary validation and invariant failures use `Result`. Release the GIL for
bulk calls with `Python::allow_threads`.

Also expose a C-ABI feature for comparison with direct PyO3 overhead. Run
`cargo test`, Clippy, rustfmt, Miri for unsafe-enabled code, and property tests
against shared conformance vectors.

## Go implementation

Workspace: `backends/go/`

Use pre-sized primitive slices for every tree field and the ring. Use integer
indexes rather than pointers so Go's garbage collector sees a small bounded set
of slice objects instead of one object per node.

Build with `-buildmode=c-shared`. C callers must hold opaque integer handles,
not Go pointers. Store instances behind `runtime/cgo.Handle` or a synchronized
handle registry and delete each handle during destruction.

Requirements:

- Recover panics at every exported C function and return
  `STREAM_STATS_WS_INTERNAL_ERROR`.
- Keep one instance single-threaded; protect only the global handle registry.
- Use explicit result/status out-parameters compatible with the canonical C
  ABI.
- Provide `add_many` because a cgo call per scalar value can dominate the AVL
  work.
- Avoid interface values and allocations in hot methods.
- Benchmark with garbage collection enabled and report full Go runtime RSS.
- Run unit tests, fuzz tests, `go vet`, and the race detector for handle
  lifecycle tests.

The Python adapter loads the shared library and registers `go`. It owns the
handle, exposes idempotent `close()` and context-manager cleanup, and never
passes a Go memory address into Python. A finalizer is leak protection only;
correctness, timely memory release, and worker/handle counts must never depend
on when a finalizer runs.

## Java implementation

Workspace: `backends/java/`

Implement the core with primitive arrays (`double[]`, `int[]`, and `long[]`).
Do not use boxed `Double`, object-per-node trees, streams, or collections in hot
paths. Use `-1` child indexes and a primitive free-index stack.

The Python integration will own a long-lived JVM worker. Use a versioned,
length-prefixed binary protocol for production and an optional JSON-lines mode
for debugging. Commands cover construction, mutation, scalar queries, bulk
addition, validation in test builds, and shutdown. Every response includes a
request identifier and status code.

All integer and binary64 fields use fixed little-endian encoding. The initial
handshake negotiates protocol major/minor version, backend build identifier,
supported commands, and maximum frame size; byte order is not negotiable.
Major-version mismatch fails startup, while compatible minor versions use
advertised capabilities.

Worker requirements:

- Support multiple isolated window handles in one JVM.
- Serialize commands per handle; instances themselves are not thread-safe.
- Bound frame lengths and reject malformed input without terminating the
  worker.
- Apply configurable startup, request, and shutdown timeouts in the Python
  adapter.
- Detect EOF, broken pipes, process exit, request-ID mismatch, and truncated
  frames. Fail all outstanding requests with a backend-worker error and mark
  affected handles closed.
- Define cancellation as best-effort: the adapter may abandon a request and
  terminate the worker, but it must not assume an interrupted mutation was
  rolled back unless the worker acknowledged cancellation before execution.
- Do not transparently replay mutating commands after a crash. Recreating an
  empty worker is allowed only when the caller explicitly creates new state.
- Write logs only to stderr so stdout remains protocol-safe.
- Surface unexpected exceptions as internal-error responses.
- Provide graceful shutdown and forceful cleanup if the Python owner exits.
- Warm the JVM before timed trials and separately report cold-start cost.
- Record heap and complete process RSS because heap-only memory is misleading.

Do not cache snapshots implicitly in the first worker adapter. An individual
property performs an individual scalar request, while callers that want one
round trip use explicit `snapshot()`. This keeps invalidation rules and scalar
benchmark interpretation unambiguous. A later cached adapter must be a
separately tested and benchmarked optimization.

Build with Gradle, publish a reproducible runnable JAR, and test on supported
LTS JVMs. Use JUnit, randomized conformance vectors, and JMH for in-JVM
algorithm measurements.

## Scala implementation

Workspace: `backends/scala/`

Implement an independent Scala core using primitive `Array[Double]`,
`Array[Int]`, and `Array[Long]`. Avoid generic collections, tuples, `Option`,
and closures in hot paths because they may box values or allocate. Return
internal status/result records only at the worker boundary.

Use the same versioned worker protocol as Java so the Python transport adapter
can be shared. The Scala worker and Java worker must remain separate selectable
backends and must not share the Java AVL implementation; sharing protocol code
and conformance fixtures is acceptable.

Build with sbt and produce an assembly JAR. Pin Scala and JVM versions in the
build. Use MUnit or ScalaTest for correctness and JMH for in-JVM benchmarks.
Inspect allocation profiles to confirm hot updates do not create boxed node or
collection objects.

The shared Java/Scala Python transport owns framing, timeout, crash detection,
and cleanup. Each worker still implements and advertises its own backend and
build identity.

## Error translation and lifecycle

Use one canonical error table across adapters:

| Condition | Python result |
|---|---|
| Invalid window size | `ValueError` |
| Non-numeric value before facade conversion | `TypeError` |
| NaN or infinity | `ValueError` |
| Empty statistic/removal | `statistics.StatisticsError` |
| Missing arbitrary value | `ValueError` |
| Allocation failure | `MemoryError` |
| Missing artifact/runtime | `BackendUnavailableError` |
| Violated internal invariant | `RuntimeError` |
| Closed engine | `RuntimeError` |
| Worker timeout/crash/protocol failure | `BackendWorkerError` (`RuntimeError`) |

Native resources should support explicit `close()` through the Python facade
and automatic finalization as leak protection only. Closing is idempotent.
Operations on a closed engine raise a consistent runtime error. Context-manager
support is required for JVM workers and recommended for native backends with
external resources. Tests must verify that correctness never depends on
finalizer timing.

## Conformance testing

Create language-neutral JSON test vectors containing:

- Window size.
- Ordered mutation commands.
- Expected eviction/removal results.
- Expected count and aggregate results after selected steps.
- Percentile and percentile-of queries.
- Expected errors.
- Absolute and relative floating-point tolerances.

Do not generate canonical expected values solely from the optimized Python
engine. Store binary64 inputs as hexadecimal strings, calculate aggregates with
high-precision `decimal.Decimal`, and use integer/rational percentile ranks
before applying the frozen binary64 rounding policy. The Python, NumPy, and all
foreign engines consume the same checked-in fixtures. Review and version fixture
generation separately from backend changes so one implementation defect cannot
become the shared oracle.

Required datasets include:

- Empty, partial, full, and repeatedly wrapped windows.
- Window size one.
- All four AVL rotation patterns plus deletion rebalancing.
- Duplicate-heavy and all-equal values.
- Ascending, descending, alternating, and fixed-seed random values.
- Signed zero normalization.
- Large offsets with small spreads.
- Values near binary64 limits and mixtures of magnitudes.
- Aggregate and interpolation overflow, infinite results, invalid internal NaN,
  and the negative-variance clamp boundary.
- Arbitrary removal at the beginning, middle, and end of arrival order.
- Repeated clear/reuse and lifecycle failure cases.
- Forked-child rejection for external native, Go, Java, and Scala engines.
- Strict arithmetic versus separately labeled FMA/optimized arithmetic builds.
- JVM timeout, malformed-frame, truncated-response, cancellation, crash, and
  non-replay behavior.

Each implementation must provide an internal invariant validator in test/debug
builds. The validator checks ordering, balance, heights, subtree metadata,
active/free slot partitioning, and equality of the tree and ring multisets.

Run three levels of tests:

1. Native unit and property/fuzz tests without Python integration.
2. Shared conformance vectors through the native public API.
3. The same vectors through the installed Python adapter.

No backend enters comparative results until all three levels pass.

## Benchmark integration

Extend `benchmarks/benchmark_operations.py` to discover installed
`WindowStatisticsEngine` backends rather than hard-code two implementations.
Retain the current sizes:

```text
16, 64, 256, 1024, 4096, 65536
```

Record three distinct benchmark layers rather than treating them as
interchangeable:

1. **Core algorithm:** native/JMH/JMH-equivalent harness with no Python, FFI,
   cgo, or worker transport.
2. **Scalar Python facade:** one Python-visible operation per bridge request,
   including the overhead users pay for ordinary calls.
3. **Bulk Python facade:** batched operations that amortize FFI, cgo, or worker
   transport and report batch size.

Plots and tables must identify the layer. Do not claim that an IPC-backed JVM
scalar result measures AVL performance; compare its core algorithm through JMH
and report Python IPC latency separately. Continue to record JSON, CSV, plots,
runtime/compiler versions, exact build flags, CPU architecture, peak RSS,
iterations, batch size, and dataset seed.

For JVM backends, record cold-start and warmed steady-state results separately.
For Go, report GC configuration. For native backends, record optimization,
target-CPU, LTO, and sanitizer status. Never compare debug or sanitizer builds
with optimized release builds.

Strict-conformance and optimized arithmetic builds must be separate benchmark
labels, particularly when FMA, fast-math, or reassociation is enabled.

## Packaging and CI

Target platforms in phases:

1. macOS arm64 and Linux x86-64.
2. Linux arm64 and Windows x86-64.
3. Additional architectures only when CI and release ownership are available.

CI should build a matrix containing relevant compilers/runtimes, execute native
tests, install produced artifacts into Python, run shared conformance tests,
and perform a short benchmark smoke test. Full performance runs belong on
dedicated, stable hardware rather than shared CI runners.

Do not require every toolchain to install the base Python package. Distribute
foreign backends as optional wheels/packages or explicitly built local plugins.
Lock toolchain versions and generate software bills of materials for release
artifacts.

Release backends independently so one toolchain or platform failure cannot
block the base package or unrelated engines:

```text
stream-stats-c
stream-stats-cpp
stream-stats-rust
stream-stats-go
stream-stats-java
stream-stats-scala
stream-stats-assembly
```

Each package declares compatible core API and engine-protocol version ranges.
The base package owns discovery and error translation but does not bundle every
runtime. A coordinated umbrella release may pin known-compatible versions
without forcing identical release cadence.

## Delivery sequence

Treat each numbered item as a gate: do not begin the next integration tier
until the current tier passes native, conformance, adapter, lifecycle, and
benchmark-smoke tests.

1. Freeze semantics, overflow behavior, zero normalization, error mapping,
   tolerance policy, and language-neutral conformance vectors.
2. Introduce the compatibility-preserving Python
   `WindowStatisticsEngine` facade/registry without changing default behavior.
3. Implement C as the canonical native ABI, including snapshot and bulk APIs,
   and validate the benchmark/plugin path.
4. Implement Rust with direct PyO3 and optional C-ABI paths; use it to validate
   that the contract is not accidentally C-specific.
5. Implement C++ with pybind11 and compare direct binding, C-ABI, scalar, and
   bulk overhead.
6. Implement Go with scalar and bulk cgo paths plus deterministic lifecycle
   management.
7. Freeze and fault-test the versioned worker protocol, then implement Java.
8. Implement Scala against the same transport semantics while retaining an
   independent Scala AVL core.
9. Evaluate assembly only for C hotspots proven by profiles. Publish the
   C/assembly hybrid backend only if it clears its performance gate.
10. Add cross-backend core/scalar/bulk plots and memory reports on stable
    hardware.
11. Package supported backends and document installation, availability,
    arithmetic mode, and platform limitations.

## Acceptance criteria

A backend is complete when it:

- Implements every `WindowStatisticsEngine` operation.
- Passes native, conformance-vector, and Python-adapter tests.
- Performs no per-node allocation during steady-state updates.
- Preserves transactional ring/tree mutation.
- Reports unavailable artifacts explicitly without Python fallback.
- Supports deterministic resource cleanup.
- Supplies scalar and appropriate bulk APIs.
- Supplies a one-call snapshot for constant-time aggregates where the bridge
  supports it.
- Defines and passes overflow, negative-zero, strict-arithmetic, and closed
  lifecycle vectors.
- Records reproducible performance and memory results for all required window
  sizes.
- Separates core algorithm, scalar facade, and bulk facade performance.
- Documents compiler/runtime versions, build flags, supported platforms, and
  known performance limitations.
