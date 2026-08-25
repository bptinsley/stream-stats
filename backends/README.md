# First-party backend workspaces

Each directory is an independently buildable implementation workspace. Keep
language toolchains and generated artifacts out of the Python source tree.

The Python implementation ships in the base package. Local artifacts are
discovered by the `stream_stats.window_statistics` registry; separately
distributed packages can publish the same factory through its entry-point
group. See `docs/BACKENDS.md` for integration and lifecycle details.

The assembly workspace is deliberately not selectable: its first publication
gate found no kernel with evidence of an end-to-end advantage over optimized C.
