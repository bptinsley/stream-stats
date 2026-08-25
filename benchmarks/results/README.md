# Recorded benchmark report

This directory contains the full operation report recorded on 2026-08-24
(America/Los_Angeles) for optimized Python, NumPy, Cython, C, C++, Rust, Go,
Java, and Scala. Cython was added through a recorded incremental run using the
same methodology. The report uses 100,000 steady-state updates, three isolated
repeats, the fixed `0x1234ABCD` dataset, and window sizes 16, 64, 256, 1,024,
4,096, and 65,536.

- [Interactive data (JSON)](window_statistics_operations.json)
- [Flat table (CSV)](window_statistics_operations.csv)
- [Performance plots (PNG)](window_statistics_operations.png)

Representative median results in nanoseconds per item:

| Implementation | Scalar add, w=16 | Bulk add, w=16 | Scalar add, w=65,536 | Bulk add, w=65,536 | Peak RSS at w=65,536 |
|---|---:|---:|---:|---:|---:|
| optimized Python | 8,580 | 8,449 | 40,048 | 27,393 | 48.0 MiB |
| NumPy baseline | 1,177 | 1,171 | 15,390 | 15,186 | 41.0 MiB |
| Cython direct | 351 | 153 | 1,203 | 633 | 41.1 MiB |
| C | 963 | 237 | 1,936 | 659 | 40.4 MiB |
| C++ | 974 | 242 | 1,857 | 671 | 40.4 MiB |
| Rust | 990 | 267 | 1,915 | 752 | 40.5 MiB |
| Go | 1,059 | 279 | 2,064 | 693 | 45.4 MiB |
| Java | 10,348 | 298 | 9,826 | 692 | 150.8 MiB |
| Scala | 10,307 | 309 | 9,985 | 735 | 152.5 MiB |

Java and Scala scalar values include one framed IPC round trip per operation;
their bulk values amortize transport over 256 items. Median cold start was
about 39 ms for Java and 94–98 ms for Scala. JVM RSS is the Python owner peak
plus live worker RSS, not heap alone. See [the full methodology](../../docs/BENCHMARKS.md)
and JSON metadata for exact toolchains, flags, iterations, and per-operation
memory values.
