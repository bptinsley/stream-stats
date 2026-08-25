# Assembly backend

Architecture-specific optimized kernels. Start with x86-64 and arm64 versions
behind the same C ABI as the C backend, with a portable C fallback used only at
build time for unsupported architectures.

No assembly backend is currently published. See [EVALUATION.md](EVALUATION.md)
for the completed macOS arm64 publication gate and rationale.
