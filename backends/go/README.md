# Go backend

Go implementation built with `-buildmode=c-shared`, plus a small Python
adapter. The adapter registers as `go` and owns cleanup of Go-side handles.
