# C backend

Canonical recycled-array AVL implementation and versioned ABI definition. The
shared library exports scalar and bulk mutation, removal, snapshot, percentile,
validation, and destruction functions. Run `make -C backends/c test` and
`make -C backends/c sanitize`.
