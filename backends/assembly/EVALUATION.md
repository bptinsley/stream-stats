# Assembly publication-gate evaluation

Status: evaluated, not published as a selectable backend.

The first target was macOS arm64. Apple Clang 21.0.0 compiled the strict C
backend with `-O3 -fno-fast-math -ffp-contract=off`. Inspection of the emitted
code confirmed scalar, non-FMA implementations for add, removal, bulk add, and
rank queries. No isolated arithmetic kernel dominated the end-to-end path;
the remaining work is AVL branching, indexed loads, rotations, and cyclic-ring
movement.

A three-repeat Python-facade gate using the fixed `0x1234ABCD` dataset measured:

| Operation | w=16 | w=256 | w=4096 |
|---|---:|---:|---:|
| add | 996 ns | 1,092 ns | 1,456 ns |
| remove(value)+add | 1,332 ns | 1,654 ns | 4,167 ns |
| remove_oldest+add | 1,407 ns | 1,500 ns | 1,902 ns |
| percentile(50) | 521 ns | 508 ns | 538 ns |

These results show the expected branch/ring-scan growth and do not identify a
kernel for which maintained handwritten assembly has a credible end-to-end
advantage over the optimizer. Consequently no `assembly` factory is
registered. This is intentional: requesting `backend="assembly"` raises
`BackendUnavailableError` rather than silently selecting C. Revisit this gate
only with hardware-counter profiles and a differential-tested kernel that
produces a statistically repeatable improvement without regressions at the
required window sizes.
