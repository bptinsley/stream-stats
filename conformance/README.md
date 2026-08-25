# Window statistics conformance vectors

`window_statistics.json` is the language-neutral behavioral contract for every
`WindowStatisticsEngine` implementation.

- Inputs are stored as exact binary64 hexadecimal strings.
- Expected floating-point results are decimal strings calculated independently
  with high-precision decimal arithmetic.
- Each comparison carries explicit absolute and relative tolerances.
- Eviction order, counts, normalized zero, and errors are exact.

Backends should consume this file from their native tests and again through
their installed Python adapter. Changes to vectors require review independent
of implementation changes.
