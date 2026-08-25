# C backend

Canonical native implementation and ABI definition. The shared library owns
window state and exports create, push, reset, result, and destroy functions.
Its ABI is the reference for the assembly, Go, and optional C++ adapters.
