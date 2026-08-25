# C++ backend

Independent C++20 recycled-array AVL implementation. It owns pre-sized
structure-of-arrays storage with `std::vector`, exposes the canonical stable C
ABI for dependency-free Python loading, and performs no per-node allocation in
steady state.

Build and test:

```sh
cmake -S backends/cpp -B backends/cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build backends/cpp/build
ctest --test-dir backends/cpp/build --output-on-failure
```
