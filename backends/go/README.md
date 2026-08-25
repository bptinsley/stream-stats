# Go backend

Independent recycled-array AVL built from pre-sized primitive slices. The
shared-library ABI stores instances in `runtime/cgo.Handle`; no Go pointer is
passed to Python.

```sh
cd backends/go
go test ./...
go build -buildmode=c-shared -o build/libstream_stats_go.dylib .
```
