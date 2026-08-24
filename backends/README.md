# First-party backend workspaces

Each directory is an independently buildable implementation workspace. Keep
language toolchains and generated artifacts out of the Python source tree.

The Python implementation is in `src/stream_stats/_python_backend.py` because
it ships in the base wheel. All other implementations publish an adapter that
registers under the matching name in the `stream_stats.backends` entry-point
group. See `docs/BACKENDS.md` for the contract and integration choices.

Backend delivery order should be C (canonical ABI), assembly and C++ (native
ABI validation), Rust and Go, then Java and Scala (persistent worker protocol).
