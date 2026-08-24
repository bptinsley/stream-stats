# Plan 1: pure-Python `WindowStatistics`

## Goal and scope

Implement the first `WindowStatistics` backend entirely in Python. It will be
the behavioral reference for later assembly, C, C++, Rust, Go, Scala, and Java
implementations.

The class will calculate exact order statistics and aggregates over a
fixed-size sliding window using two bounded structures:

1. A preallocated, index-addressed AVL multiset augmented with subtree
   statistics.
2. A preallocated cyclic array containing values in arrival order.

The cyclic array identifies the oldest value when the window is full. The AVL
tree removes one occurrence of that value before inserting the new value. Tree
slots are recycled through a free-index stack, eliminating repeated allocation
and destruction of node objects during steady-state use.

Throughout this document, `w` is the configured window size, `n` is the current
number of values (`0 <= n <= w`), and `d` is the number of distinct values
(`d <= n`).

## Proposed public API

```python
class WindowStatistics:
    def __init__(self, window_size: int) -> None: ...

    def add(self, value: float) -> float | None: ...
    def remove_oldest(self) -> float: ...
    def remove(self, value: float) -> None: ...
    def clear(self) -> None: ...

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

    @property
    def standard_deviation(self) -> float: ...

    def percentile(self, percentile: float) -> float: ...
    def percentile_of(self, value: float) -> float: ...
```

`add(value)` inserts the newest value. If the window is full, it first evicts
and returns the oldest value; otherwise it returns `None`.

`remove_oldest()` removes and returns the oldest value. This is the efficient
explicit-removal operation and should be preferred for normal window use.

`remove(value)` removes the oldest matching occurrence from both structures.
Maintaining arrival order requires a cyclic-array scan and compaction, so this
method is intentionally slower. It raises `ValueError` when the value is not
present. Tree-only removal remains a private `O(log w)` operation.

`clear()` empties the class without releasing its preallocated storage.

`std` is the canonical standard-deviation name because it matches the existing
`Window` API. `standard_deviation` is a readable alias returning the same
value.

Empty-window queries raise `statistics.StatisticsError`, except `count` and
`sum`, which return `0` and `0.0`.

### Value rules

- Accept real numeric values that can be converted to finite Python `float`.
- Reject `bool`, `NaN`, positive infinity, and negative infinity.
- Rejecting `NaN` is essential because it does not define a consistent total
  ordering and would violate binary-search-tree invariants.
- Store the converted float in both the tree and cyclic array so eviction uses
  exactly the same key that was inserted.
- Reject a boolean, non-integer, or non-positive `window_size` before
  allocating. Do not impose an arbitrary fixed maximum. If a valid but very
  large request cannot be allocated, allow `MemoryError` to propagate without
  publishing a partially initialized instance.

### Percentile definitions

`percentile(p)` accepts `p` in `[0, 100]`. It uses linear interpolation at
fractional zero-based rank:

```text
rank = (p / 100) * (n - 1)
result = lower_value + fraction * (upper_value - lower_value)
```

The tree selects the lower and upper ranked values without sorting the window.
The endpoints therefore satisfy `percentile(0) == min` and
`percentile(100) == max`.

`percentile_of(value)` uses duplicate-aware midrank:

```text
100 * (count(x < value) + 0.5 * count(x == value)) / n
```

Midrank avoids assigning every duplicate to either extreme of its tied range.
For a value below the minimum it returns `0.0`; for a value above the maximum
it returns `100.0`. A future API can add strict and inclusive empirical-CDF
modes if users need those different semantics.

## Preallocated indexed AVL tree

### Why indexes instead of node objects

The maximum number of distinct keys is bounded by `w`, so allocate capacity
once. Children and the root are integer indexes; `-1` is the null index. This
avoids allocating a new Python node on each new-key insertion and later
deallocating it on removal. It also maps cleanly to contiguous representations
in native backends.

Python containers have non-obvious tradeoffs. Fixed-length lists store pointers
to boxed numbers and can consume substantially more memory than native arrays.
`array.array` is compact but boxes values on access and may be slower. Pooled
`__slots__` nodes reduce array indexing but have poorer locality. Before
freezing the representation, implement focused indexed-array and pooled-node
prototypes behind the same private interface and benchmark fixed lists,
`array.array`, and slotted nodes.

The indexed-list design remains the baseline because it is clear, bounded, and
maps directly to native backends. Python arithmetic still creates temporary
numeric objects, so recycling removes node-allocation churn rather than
claiming that every update is allocation-free.

Parallel arrays of length `w` hold:

```text
keys                 distinct float key
multiplicities       occurrences of the key
left                 left-child index
right                right-child index
heights              AVL height
subtree_counts       occurrences in subtree, including duplicates
subtree_sums         sum of values in subtree
subtree_means        centered-moment mean for subtree
subtree_m2           sum of squared deviations from subtree mean
subtree_mins         smallest key in subtree
subtree_maxes        largest key in subtree
```

The tree also owns:

```text
root                 root index, or -1
free_indexes         stack of unused indexes
distinct_count       number of active indexes
```

Initialize `free_indexes` with every slot. Inserting a previously unseen key
pops one index and initializes all its fields. Removing the last occurrence of
a key clears its child indexes and multiplicity, then pushes the index back.
AVL rotations only relink indexes and recompute metadata; they never allocate
or release slots.

Duplicates increment or decrement `multiplicities[index]`. They do not occupy
additional nodes. Consequently, a window containing one repeated value uses
one active tree slot while still reporting the full occurrence count.

### Aggregate metadata

After insertion, deletion, or rotation, recompute a node from its children and
its repeated-key group. The root then provides count, sum, mean, variance,
minimum, and maximum in constant time.

For numerical stability, do not calculate variance as
`sum_of_squares / count - mean**2`. Store centered moments and merge the left
subtree, current repeated-key group, and right subtree using the parallel
variance formula. For aggregates `A` and `B`:

```text
n = A.count + B.count
delta = B.mean - A.mean
mean = A.mean + delta * B.count / n
M2 = A.M2 + B.M2 + delta**2 * A.count * B.count / n
```

The repeated-key group has `count = multiplicity`, `mean = key`, and `M2 = 0`.
Empty aggregates act as identities. Population variance is `root.M2 / n` and
population standard deviation is its square root.

Maintain `subtree_sums` separately for the public `sum`; use `math.fsum` when
combining the left sum, repeated-key sum, and right sum. Centered moments
substantially reduce catastrophic cancellation, although binary floating-point
results still have rounding error. Clamp a negative variance to zero only when
it is within a documented rounding tolerance; treat a material negative value
as an invariant failure.

AVL rotations change the grouping order of moment merges. This is
mathematically equivalent, but floating-point association can change the final
bits. Correctness and cross-backend tests must use documented absolute and
relative tolerances rather than require bit-for-bit equality.

Long-running eviction workloads may accumulate numerical drift. Tests should
continuously compare results with a fresh calculation from the cyclic array. If
drift exceeds the documented tolerance, add an internal periodic `O(w)` rebuild
based on update count. Do not add rebuild cost or public configuration until
measurements show it is necessary.

`subtree_mins` and `subtree_maxes` make both extreme queries `O(1)` without
holding cached node references that could become stale after deletion.

### Transactional mutation

Validate and convert an incoming value before changing either structure.
`add()` should then follow a mutation sequence with a known rollback:

1. Read but do not overwrite the outgoing ring value.
2. Remove the outgoing tree occurrence when the window is full.
3. Insert the new tree occurrence.
4. Commit the ring overwrite and advance its indexes.

Expected user errors must occur before step 2. An internal failure during tree
mutation indicates a violated invariant; development builds should restore a
removed occurrence where possible and raise an internal consistency error.
Committing ring state last prevents it from advertising a value the tree does
not contain. Apply the same principle to both removal methods.

### Order-statistic operations

Rank selection descends using the left child's `subtree_count` and the current
node's multiplicity. It finds a value at any zero-based sorted occurrence rank
in `O(log w)`.

`percentile_of(value)` descends once, accumulating occurrence counts for keys
strictly below the supplied value and reading the matching multiplicity when
present. This is also `O(log w)`.

## Preallocated cyclic array

Allocate exactly `w` value slots and maintain:

```text
ring                 fixed-length list of floats
head                 index of oldest active value
length               number of active values
capacity             configured window size
```

Appending before full writes to `(head + length) % capacity`. Appending when
full reads the key at `head`, removes one occurrence from the tree, overwrites
the slot, advances `head`, and inserts the new key into the tree. Oldest-value
discovery and ring mutation are `O(1)`.

Arbitrary `remove(value)` scans active entries from oldest to newest. After
finding the first match, it shifts later logical entries toward the head to
preserve arrival order, then removes one occurrence from the tree. This costs
`O(w)`. Do not add tombstones or per-value position maps initially; those make
normal eviction and duplicate bookkeeping more complex without benefiting the
primary sliding-window use case.

## Exact versus approximate results

The tree and cyclic array retain every window value. No requested result needs
an algorithmic approximation, sketch, histogram, or sample.

| Result | Algorithmic status | Numerical qualification |
|---|---|---|
| Count | Exact | Python integers do not overflow at practical window sizes |
| Sum | Exact aggregation of stored items | Float addition and conversion can round |
| Minimum/maximum | Exact | Exact relative to stored float keys |
| Mean | Exact aggregation of stored items | Float merge/division can round |
| Variance | Exact population definition | Centered-moment float operations can round |
| Standard deviation | Exact population definition | Variance and square root can round |
| Percentile | Exact ranked selection and defined interpolation | Interpolation can round |
| Percentile of value | Exact counts and midrank | Final division can round |

Approximate quantile algorithms become relevant only if a later design must
use less than `O(w)` memory, merge distributed summaries, or trade accuracy for
higher throughput. They should be separate backends or classes rather than a
silent behavior change in `WindowStatistics`.

## NumPy baseline implementation

Implement a second pure-Python class, `NumpyWindowStatistics`, as a deliberately
simple baseline. It must expose the same public API, validation, empty-window
behavior, population-statistic definitions, percentile interpolation, and
midrank `percentile_of` semantics as `WindowStatistics`.

This baseline must not use the AVL tree, node recycling, a cyclic buffer,
incremental moments, cached aggregates, monotonic queues, or other custom
optimization. It stores the current window directly as a one-dimensional
NumPy `float64` array in arrival order:

```python
class NumpyWindowStatistics:
    def __init__(self, window_size: int) -> None:
        self.window_size = window_size
        self._values = numpy.empty(0, dtype=numpy.float64)

    def add(self, value: float) -> float | None:
        # Validate first, append with numpy.append, and retain the newest w.
        ...

    def remove_oldest(self) -> float:
        # Return element zero and replace storage with self._values[1:].copy().
        ...

    def remove(self, value: float) -> None:
        # Find the first equal element and remove it with numpy.delete.
        ...
```

`add` may use `numpy.append` and slice/copy the newest `w` entries. This
intentionally allocates and copies rather than reproducing the optimized
implementation's ring buffer. `remove_oldest` removes index zero, and
`remove(value)` removes the first matching index so duplicate behavior agrees
with the primary class.

Calculate every return value directly from the current array at query time:

```text
count                  values.size
sum                    numpy.sum(values, dtype=numpy.float64)
min                    numpy.min(values)
max                    numpy.max(values)
mean                   numpy.mean(values, dtype=numpy.float64)
variance               numpy.var(values, ddof=0, dtype=numpy.float64)
std                     numpy.std(values, ddof=0, dtype=numpy.float64)
percentile(p)           numpy.percentile(values, p, method="linear")
percentile_of(value)    100 * (count(values < value)
                               + 0.5 * count(values == value)) / values.size
```

`standard_deviation` remains an alias of `std`. The baseline must not cache any
of these results. NumPy may internally use vectorized native code, pairwise
reductions, partitioning, or temporary arrays; those are library internals, not
custom data structures in this project.

NumPy should be an optional baseline/benchmark dependency rather than a
requirement of the core package. Document and package it through a dedicated
extra such as:

```toml
[project.optional-dependencies]
baseline = ["numpy>=2.0"]
```

When NumPy is absent, importing the main `stream_stats` package must continue
to work. Import it lazily when the baseline class is requested and raise an
actionable optional-dependency error.

### NumPy baseline complexity

The table reports work visible from the baseline design. NumPy's exact
percentile algorithm is an implementation detail, so its conservative bound is
shown separately from ordinary linear reductions.

| Baseline method or value | Time | Additional temporary space | Explanation |
|---|---:|---:|---|
| Constructor | `O(1)` | `O(1)` | Creates an empty NumPy array |
| `add(value)` | `O(w)` | `O(w)` | Allocates and copies the array |
| `remove_oldest()` | `O(w)` | `O(w)` | Slices and copies remaining values |
| `remove(value)` | `O(w)` | `O(w)` | Linear match plus `numpy.delete` copy |
| `clear()` | `O(1)` | `O(1)` | Replaces storage with an empty array |
| `count` | `O(1)` | `O(1)` | Reads array size |
| `sum` | `O(w)` | implementation-dependent | NumPy reduction |
| `min` / `max` | `O(w)` | implementation-dependent | NumPy reduction |
| `mean` | `O(w)` | implementation-dependent | NumPy reduction |
| `variance` | `O(w)` | implementation-dependent | NumPy population variance |
| `std` / `standard_deviation` | `O(w)` | implementation-dependent | Variance reduction plus square root |
| `percentile(p)` | implementation-dependent; conservatively `O(w log w)` | up to `O(w)` | NumPy may select, partition, sort, and interpolate |
| `percentile_of(value)` | `O(w)` | up to `O(w)` | Vectorized comparisons and counts |

The retained array uses `O(w)` space. The primary comparison is therefore not
asymptotic storage but update/query cost, allocation behavior, interpreter
overhead, native vectorization, and crossover points at different window and
query sizes.

### Baseline comparison rules

1. Run identical input and mutation sequences through both implementations.
2. Require identical counts, eviction return values, exception types, and
   percentile definitions.
3. Compare floating-point results with the same absolute and relative
   tolerances used for language-backend conformance.
4. Benchmark update-only, query-after-every-update, and batched-query workloads.
   NumPy may be competitive when queries amortize native vectorization even
   though each update or query is `O(w)`.
5. Report peak RSS and Python-visible allocation metrics as well as throughput.
6. Label this class as a correctness/performance baseline, not an automatic
   fallback when the optimized implementation fails.

## Time complexity

AVL height is `O(log d)` and `d <= w`, so worst-case tree operations are
reported as `O(log w)`.

| Method or calculated value | Worst-case time | Explanation |
|---|---:|---|
| Constructor | `O(w)` | Allocates all tree arrays, free indexes, and ring slots |
| `add(value)`, window not full | `O(log w)` | Ring append is `O(1)`; AVL insert dominates |
| `add(value)`, window full | `O(log w)` | One AVL removal and insertion; ring work is `O(1)` |
| `remove_oldest()` | `O(log w)` | Ring lookup/update is `O(1)`; AVL removal dominates |
| `remove(value)` | `O(w + log w)`, simplified to `O(w)` | Ring scan/compaction plus AVL removal |
| `clear()` | `O(w)` | Reinitializes the free-index stack and active metadata |
| `count` | `O(1)` | Root metadata or ring length |
| `sum` | `O(1)` | Root metadata |
| `min` | `O(1)` | Root `subtree_min` |
| `max` | `O(1)` | Root `subtree_max` |
| `mean` | `O(1)` | Root centered mean |
| `variance` | `O(1)` | Root M2 divided by root count |
| `std` / `standard_deviation` | `O(1)` | Square root of variance |
| `percentile(p)` | `O(log w)` | One or two ranked selections |
| `percentile_of(value)` | `O(log w)` | One augmented tree descent |

Two adjacent selections for an interpolated percentile remain `O(log w)`
because the constant factor does not change asymptotic complexity. A future
batched percentile method should sort requested ranks and traverse once, but
is outside the first implementation.

## Space and allocation complexity

| Structure | Space | Allocation behavior after construction |
|---|---:|---|
| Cyclic array | `O(w)` | Reuses fixed slots |
| AVL parallel arrays | `O(w)` | Reuses fixed slots |
| Free-index stack | `O(w)` | Pops and pushes existing indexes |
| AVL ancestor path | `O(log w)` | Reuse one preallocated path buffer if practical |
| Total retained space | `O(w)` | No node-object churn in steady state |

The implementation should preallocate an ancestor-index path of AVL maximum
height or reuse a single internal list. Because a mutable statistics instance
is not thread-safe, reusing this scratch path is acceptable. Public methods
should document that callers need external synchronization when sharing one
instance across threads.

## Concerns and decisions

1. **Pure-Python overhead:** Indexing several parallel Python lists may or may
   not outperform slotted node objects. The indexed pool is still preferred
   for predictable storage and portability, but benchmark both representations
   before claiming a speed advantage.
2. **Numerical stability:** Use mergeable centered moments, not raw sum of
   squares, and validate against `statistics.pvariance` on difficult inputs.
3. **Duplicate keys:** Store one node plus multiplicity. Test every removal
   transition from many occurrences to one and from one to zero.
4. **Arbitrary removal:** Keep it correct but `O(w)`. Optimize only if profiling
   shows it is a real workload requirement.
5. **Percentile ambiguity:** Freeze linear interpolation and midrank semantics
   in tests so later backends cannot choose subtly different definitions.
6. **Reentrancy and threads:** A class instance is mutable and not thread-safe.
   Separate instances are independent.
7. **Backend conformance:** Python behavior, exceptions, empty-window rules,
   float acceptance, and percentile definitions form the cross-language
   contract.
8. **Floating-point conformance:** Rotations and backend implementations may
   associate moment merges differently. Compare results using stated absolute
   and relative tolerances; require exact agreement only for counts and
   structural behavior.
9. **Mutation atomicity:** Validate inputs before mutation and commit cyclic
   state last. Unexpected mid-mutation failures are invariant errors, not
   ordinary user errors.
10. **Allocation limits:** Construction is `O(w)` and may raise `MemoryError`.
    Do not expose the instance until every required structure is initialized.

## Correctness and validation plan

1. Test the cyclic array independently across empty, partial, full, and many
   wraparound cycles.
2. After every randomized mutation, recursively verify AVL ordering, balance,
   heights, multiplicities, every subtree aggregate, slot uniqueness, and the
   free/active index partition.
3. Force all four insertion and deletion rotation shapes.
4. Compare snapshots with a straightforward Python list using `math.fsum`,
   `statistics.pvariance`, `min`, `max`, and sorted percentile calculations;
   also run the same vectors through `NumpyWindowStatistics`.
5. Cover duplicates, all-equal windows, ascending and descending streams,
   window size one, signed zero, and removal of a key's final occurrence.
6. Test percentile endpoints, interpolation, ties, and `percentile_of` inputs
   below, within, and above the observed range.
7. Test large offsets with small spreads, mixed magnitudes, and long eviction
   sequences to quantify numerical error and determine whether periodic
   rebuilding is justified.
8. Use property-based tests for random add, automatic eviction,
   `remove_oldest`, arbitrary remove, and clear sequences.
9. Benchmark indexed arrays against slotted nodes, a `bisect`-maintained sorted
   list, direct recalculation, and the current rolling-statistics backend at
   small, medium, and large window sizes. Record throughput and retained memory
   before choosing the Python storage representation.
10. Reuse the finalized vectors as conformance tests for every later language
    backend.
11. Inject internal failures in development tests to verify rejected inputs do
    not mutate state and rollback preserves ring/tree agreement.
12. Expose a private `_validate()` development helper that checks AVL ordering,
    balance, aggregates, recycled indexes, and ring/tree multiset equality.
13. Test that the core package imports without NumPy installed and that
    requesting the baseline produces an actionable dependency error.

## Implementation sequence

1. Implement the fixed cyclic array and its invariant tests.
2. Implement `NumpyWindowStatistics` and use it alongside the list-based oracle
   as the intentionally unoptimized public baseline.
3. Prototype the preallocated indexed AVL pool and pooled slotted-node storage
   behind one private interface; benchmark allocation, retained memory, lookup,
   insertion, and deletion before selecting the Python representation.
4. Add augmented count, sum, min, max, centered mean, and M2 recomputation.
5. Add rank selection, linear-interpolated percentile, and midrank
   `percentile_of`.
6. Compose the structures in the pure-Python `WindowStatistics` class.
7. Add cross-implementation comparisons, randomized tests, and
   numerical-stability tests.
8. Add long-running drift tests and implement periodic rebuilding only if
   measured error exceeds the documented tolerance.
9. Add mutation-failure tests and finalize transactional update behavior.
10. Benchmark optimized, NumPy-baseline, and list-oracle workloads across
    window sizes and query frequencies.
11. Publish Python behavior and floating-point tolerances as the conformance
   contract for other backends.
