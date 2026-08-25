package main

/*
#include <stdint.h>
#include <stdlib.h>
typedef struct {
    uint32_t abi_version; uint32_t struct_size; uint64_t count;
    double sum, min, max, mean, variance, std;
    uint8_t has_values; uint8_t reserved[7];
} stream_stats_ws_snapshot;
static const char *stream_stats_go_last_error(void) {
    return "Go backend operation failed";
}
*/
import "C"

import (
	"errors"
	"math"
	"runtime/cgo"
	"sort"
	"unsafe"
)

const nilIndex int32 = -1
const (
	ok C.int32_t = iota
	invalid
	empty
	notFound
	outOfMemory
	internal
)

type windowError struct{ status C.int32_t }

func (e windowError) Error() string { return "window statistics error" }

type moments struct {
	count    uint64
	mean, m2 float64
}

type WindowStatistics struct {
	capacity, head, length                   int
	root                                     int32
	keys, sums, means, m2, mins, maxes, ring []float64
	multiplicity, counts                     []uint64
	left, right, free                        []int32
	heights                                  []uint32
}

func newWindow(capacity int) (*WindowStatistics, error) {
	if capacity <= 0 || uint64(capacity) > uint64(math.MaxInt32) {
		return nil, windowError{invalid}
	}
	w := &WindowStatistics{capacity: capacity, root: nilIndex,
		keys: make([]float64, capacity), sums: make([]float64, capacity), means: make([]float64, capacity), m2: make([]float64, capacity), mins: make([]float64, capacity), maxes: make([]float64, capacity), ring: make([]float64, capacity),
		multiplicity: make([]uint64, capacity), counts: make([]uint64, capacity), left: make([]int32, capacity), right: make([]int32, capacity), heights: make([]uint32, capacity), free: make([]int32, 0, capacity)}
	for i := 0; i < capacity; i++ {
		w.left[i], w.right[i] = nilIndex, nilIndex
	}
	for i := capacity - 1; i >= 0; i-- {
		w.free = append(w.free, int32(i))
	}
	return w, nil
}
func normalize(v float64) (float64, error) {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0, windowError{invalid}
	}
	if v == 0 {
		return 0, nil
	}
	return v, nil
}
func (w *WindowStatistics) height(n int32) uint32 {
	if n == nilIndex {
		return 0
	}
	return w.heights[n]
}
func (w *WindowStatistics) countAt(n int32) uint64 {
	if n == nilIndex {
		return 0
	}
	return w.counts[n]
}
func (w *WindowStatistics) aggregate(n int32) moments {
	if n == nilIndex {
		return moments{}
	}
	return moments{w.counts[n], w.means[n], w.m2[n]}
}
func merge(a, b moments) moments {
	if a.count == 0 {
		return b
	}
	if b.count == 0 {
		return a
	}
	count := a.count + b.count
	total := float64(count)
	d := b.mean - a.mean
	return moments{count, a.mean*(float64(a.count)/total) + b.mean*(float64(b.count)/total), a.m2 + b.m2 + d*d*float64(a.count)*(float64(b.count)/total)}
}
func (w *WindowStatistics) pull(n int32) {
	l, r, key := w.left[n], w.right[n], w.keys[n]
	all := merge(merge(w.aggregate(l), moments{w.multiplicity[n], key, 0}), w.aggregate(r))
	w.counts[n], w.means[n], w.m2[n] = all.count, all.mean, all.m2
	w.sums[n] = (func() float64 {
		if l < 0 {
			return 0
		}
		return w.sums[l]
	})() + key*float64(w.multiplicity[n]) + (func() float64 {
		if r < 0 {
			return 0
		}
		return w.sums[r]
	})()
	if l < 0 {
		w.mins[n] = key
	} else {
		w.mins[n] = w.mins[l]
	}
	if r < 0 {
		w.maxes[n] = key
	} else {
		w.maxes[n] = w.maxes[r]
	}
	lh, rh := w.height(l), w.height(r)
	if rh > lh {
		lh = rh
	}
	w.heights[n] = lh + 1
}
func (w *WindowStatistics) acquire(key float64) (int32, error) {
	if len(w.free) == 0 {
		return 0, windowError{internal}
	}
	n := w.free[len(w.free)-1]
	w.free = w.free[:len(w.free)-1]
	w.keys[n] = key
	w.multiplicity[n] = 1
	w.left[n], w.right[n] = nilIndex, nilIndex
	w.heights[n] = 1
	w.counts[n] = 1
	w.sums[n], w.means[n], w.mins[n], w.maxes[n] = key, key, key, key
	w.m2[n] = 0
	return n, nil
}
func (w *WindowStatistics) release(n int32) {
	w.multiplicity[n], w.counts[n] = 0, 0
	w.left[n], w.right[n] = nilIndex, nilIndex
	w.heights[n] = 0
	w.free = append(w.free, n)
}
func (w *WindowStatistics) balance(n int32) int {
	return int(w.height(w.left[n])) - int(w.height(w.right[n]))
}
func (w *WindowStatistics) rotateLeft(n int32) int32 {
	p := w.right[n]
	mid := w.left[p]
	w.left[p] = n
	w.right[n] = mid
	w.pull(n)
	w.pull(p)
	return p
}
func (w *WindowStatistics) rotateRight(n int32) int32 {
	p := w.left[n]
	mid := w.right[p]
	w.right[p] = n
	w.left[n] = mid
	w.pull(n)
	w.pull(p)
	return p
}
func (w *WindowStatistics) rebalance(n int32) int32 {
	w.pull(n)
	b := w.balance(n)
	if b > 1 {
		if w.balance(w.left[n]) < 0 {
			w.left[n] = w.rotateLeft(w.left[n])
		}
		return w.rotateRight(n)
	}
	if b < -1 {
		if w.balance(w.right[n]) > 0 {
			w.right[n] = w.rotateRight(w.right[n])
		}
		return w.rotateLeft(n)
	}
	return n
}
func (w *WindowStatistics) insertAt(n int32, key float64) (int32, error) {
	if n < 0 {
		return w.acquire(key)
	}
	var err error
	if key < w.keys[n] {
		w.left[n], err = w.insertAt(w.left[n], key)
	} else if key > w.keys[n] {
		w.right[n], err = w.insertAt(w.right[n], key)
	} else {
		w.multiplicity[n]++
		w.pull(n)
		return n, nil
	}
	if err != nil {
		return n, err
	}
	return w.rebalance(n), nil
}
func (w *WindowStatistics) minimum(n int32) int32 {
	for w.left[n] >= 0 {
		n = w.left[n]
	}
	return n
}
func (w *WindowStatistics) eraseAt(n int32, key float64, all bool) (int32, error) {
	if n < 0 {
		return n, windowError{notFound}
	}
	var err error
	if key < w.keys[n] {
		w.left[n], err = w.eraseAt(w.left[n], key, all)
	} else if key > w.keys[n] {
		w.right[n], err = w.eraseAt(w.right[n], key, all)
	} else if w.multiplicity[n] > 1 && !all {
		w.multiplicity[n]--
		w.pull(n)
		return n, nil
	} else {
		l, r := w.left[n], w.right[n]
		if l < 0 || r < 0 {
			replacement := l
			if l < 0 {
				replacement = r
			}
			w.release(n)
			return replacement, nil
		}
		s := w.minimum(r)
		w.keys[n], w.multiplicity[n] = w.keys[s], w.multiplicity[s]
		w.right[n], err = w.eraseAt(r, w.keys[n], true)
	}
	if err != nil {
		return n, err
	}
	return w.rebalance(n), nil
}
func (w *WindowStatistics) insert(v float64) error {
	var e error
	w.root, e = w.insertAt(w.root, v)
	return e
}
func (w *WindowStatistics) erase(v float64) error {
	var e error
	w.root, e = w.eraseAt(w.root, v, false)
	return e
}
func (w *WindowStatistics) add(v float64) (*float64, error) {
	v, e := normalize(v)
	if e != nil {
		return nil, e
	}
	if w.length < w.capacity {
		if e = w.insert(v); e != nil {
			return nil, e
		}
		w.ring[(w.head+w.length)%w.capacity] = v
		w.length++
		return nil, nil
	}
	old := w.ring[w.head]
	if e = w.erase(old); e != nil {
		return nil, e
	}
	if e = w.insert(v); e != nil {
		_ = w.insert(old)
		return nil, e
	}
	w.ring[w.head] = v
	w.head = (w.head + 1) % w.capacity
	return &old, nil
}
func (w *WindowStatistics) removeOldest() (float64, error) {
	if w.length == 0 {
		return 0, windowError{empty}
	}
	old := w.ring[w.head]
	if e := w.erase(old); e != nil {
		return 0, e
	}
	w.head = (w.head + 1) % w.capacity
	w.length--
	if w.length == 0 {
		w.head = 0
	}
	return old, nil
}
func (w *WindowStatistics) remove(v float64) error {
	v, e := normalize(v)
	if e != nil {
		return e
	}
	pos := -1
	for i := 0; i < w.length; i++ {
		if w.ring[(w.head+i)%w.capacity] == v {
			pos = i
			break
		}
	}
	if pos < 0 {
		return windowError{notFound}
	}
	if e = w.erase(v); e != nil {
		return e
	}
	for i := pos; i+1 < w.length; i++ {
		w.ring[(w.head+i)%w.capacity] = w.ring[(w.head+i+1)%w.capacity]
	}
	w.length--
	if w.length == 0 {
		w.head = 0
	}
	return nil
}
func (w *WindowStatistics) clear() {
	for i := range w.keys {
		w.multiplicity[i], w.counts[i], w.heights[i] = 0, 0, 0
		w.left[i], w.right[i] = nilIndex, nilIndex
	}
	w.free = w.free[:0]
	for i := w.capacity - 1; i >= 0; i-- {
		w.free = append(w.free, int32(i))
	}
	w.root, w.head, w.length = nilIndex, 0, 0
}
func (w *WindowStatistics) variance() (float64, error) {
	if w.length == 0 {
		return 0, windowError{empty}
	}
	v := w.m2[w.root] / float64(w.length)
	if v < 0 && v > -1e-15 {
		v = 0
	}
	if v < 0 {
		return 0, windowError{internal}
	}
	return v, nil
}
func (w *WindowStatistics) selectRank(rank uint64) float64 {
	n := w.root
	for {
		left := w.countAt(w.left[n])
		if rank < left {
			n = w.left[n]
		} else if rank < left+w.multiplicity[n] {
			return w.keys[n]
		} else {
			rank -= left + w.multiplicity[n]
			n = w.right[n]
		}
	}
}
func (w *WindowStatistics) percentile(p float64) (float64, error) {
	if w.length == 0 {
		return 0, windowError{empty}
	}
	if math.IsNaN(p) || math.IsInf(p, 0) || p < 0 || p > 100 {
		return 0, windowError{invalid}
	}
	rank := p / 100 * float64(w.length-1)
	lo, hi := uint64(math.Floor(rank)), uint64(math.Ceil(rank))
	a := w.selectRank(lo)
	if lo == hi {
		return a, nil
	}
	f := rank - float64(lo)
	return a*(1-f) + w.selectRank(hi)*f, nil
}
func (w *WindowStatistics) percentileOf(v float64) (float64, error) {
	if w.length == 0 {
		return 0, windowError{empty}
	}
	v, e := normalize(v)
	if e != nil {
		return 0, e
	}
	var less, equal uint64
	n := w.root
	for n >= 0 {
		if v < w.keys[n] {
			n = w.left[n]
		} else if v > w.keys[n] {
			less += w.countAt(w.left[n]) + w.multiplicity[n]
			n = w.right[n]
		} else {
			less += w.countAt(w.left[n])
			equal = w.multiplicity[n]
			break
		}
	}
	return 100 * (float64(less) + .5*float64(equal)) / float64(w.length), nil
}
func (w *WindowStatistics) validate() error {
	active := make([]bool, w.capacity)
	treeValues := make([]float64, 0, w.length)
	var walk func(int32, *float64, *float64) (uint32, uint64, error)
	walk = func(n int32, lower, upper *float64) (uint32, uint64, error) {
		if n == nilIndex {
			return 0, 0, nil
		}
		if n < 0 || int(n) >= w.capacity || active[n] || w.multiplicity[n] == 0 {
			return 0, 0, windowError{internal}
		}
		key := w.keys[n]
		if lower != nil && key <= *lower || upper != nil && key >= *upper {
			return 0, 0, windowError{internal}
		}
		active[n] = true
		lh, lc, err := walk(w.left[n], lower, &key)
		if err != nil {
			return 0, 0, err
		}
		for i := uint64(0); i < w.multiplicity[n]; i++ {
			treeValues = append(treeValues, key)
		}
		rh, rc, err := walk(w.right[n], &key, upper)
		if err != nil {
			return 0, 0, err
		}
		height := lh
		if rh > height {
			height = rh
		}
		height++
		count := lc + w.multiplicity[n] + rc
		if lh > rh+1 || rh > lh+1 || w.heights[n] != height || w.counts[n] != count {
			return 0, 0, windowError{internal}
		}
		return height, count, nil
	}
	_, count, err := walk(w.root, nil, nil)
	if err != nil || count != uint64(w.length) {
		return windowError{internal}
	}
	freeSeen := make([]bool, w.capacity)
	activeCount := 0
	for _, used := range active {
		if used {
			activeCount++
		}
	}
	if activeCount+len(w.free) != w.capacity {
		return windowError{internal}
	}
	for _, n := range w.free {
		if n < 0 || int(n) >= w.capacity || active[n] || freeSeen[n] {
			return windowError{internal}
		}
		freeSeen[n] = true
	}
	ringValues := make([]float64, w.length)
	for i := 0; i < w.length; i++ {
		ringValues[i] = w.ring[(w.head+i)%w.capacity]
	}
	sort.Float64s(ringValues)
	for i := range treeValues {
		if treeValues[i] != ringValues[i] {
			return windowError{internal}
		}
	}
	return nil
}
func guarded(operation func() error) (status C.int32_t) {
	status = ok
	defer func() {
		if recover() != nil {
			status = internal
		}
	}()
	if e := operation(); e != nil {
		var we windowError
		if errors.As(e, &we) {
			return we.status
		}
		return internal
	}
	return
}
func get(handle unsafe.Pointer) (*WindowStatistics, error) {
	if handle == nil {
		return nil, windowError{invalid}
	}
	token := *(*C.uintptr_t)(handle)
	return cgo.Handle(uintptr(token)).Value().(*WindowStatistics), nil
}
func writeSnapshot(w *WindowStatistics, out *C.stream_stats_ws_snapshot) error {
	if out == nil || out.abi_version != 1 || uint64(out.struct_size) < uint64(C.sizeof_stream_stats_ws_snapshot) {
		return windowError{invalid}
	}
	out.count = C.uint64_t(w.length)
	if w.length == 0 {
		out.sum = 0
		out.has_values = 0
		return nil
	}
	v, e := w.variance()
	if e != nil {
		return e
	}
	out.sum = C.double(w.sums[w.root])
	out.min = C.double(w.mins[w.root])
	out.max = C.double(w.maxes[w.root])
	out.mean = C.double(w.means[w.root])
	out.variance = C.double(v)
	out.std = C.double(math.Sqrt(v))
	out.has_values = 1
	return nil
}

//export stream_stats_ws_abi_version
func stream_stats_ws_abi_version() C.uint32_t { return 1 }

//export stream_stats_ws_last_error
func stream_stats_ws_last_error() *C.char { return C.stream_stats_go_last_error() }

//export stream_stats_ws_create
func stream_stats_ws_create(size C.uint64_t, out *unsafe.Pointer) C.int32_t {
	return guarded(func() error {
		if out == nil || uint64(size) > uint64(^uint(0)>>1) {
			return windowError{invalid}
		}
		w, e := newWindow(int(size))
		if e != nil {
			return e
		}
		h := cgo.NewHandle(w)
		token := C.malloc(C.size_t(unsafe.Sizeof(C.uintptr_t(0))))
		if token == nil {
			h.Delete()
			return windowError{outOfMemory}
		}
		*(*C.uintptr_t)(token) = C.uintptr_t(h)
		*out = token
		return nil
	})
}

//export stream_stats_ws_destroy
func stream_stats_ws_destroy(raw unsafe.Pointer) {
	if raw != nil {
		token := *(*C.uintptr_t)(raw)
		cgo.Handle(uintptr(token)).Delete()
		C.free(raw)
	}
}

//export stream_stats_ws_clear
func stream_stats_ws_clear(raw unsafe.Pointer) C.int32_t {
	return guarded(func() error {
		w, e := get(raw)
		if e == nil {
			w.clear()
		}
		return e
	})
}

//export stream_stats_ws_add
func stream_stats_ws_add(raw unsafe.Pointer, value C.double, did *C.uint8_t, evicted *C.double) C.int32_t {
	return guarded(func() error {
		if did == nil || evicted == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		v, e := w.add(float64(value))
		if e != nil {
			return e
		}
		if v == nil {
			*did = 0
			*evicted = 0
		} else {
			*did = 1
			*evicted = C.double(*v)
		}
		return nil
	})
}

//export stream_stats_ws_add_many
func stream_stats_ws_add_many(raw unsafe.Pointer, values *C.double, length C.uint64_t, did *C.uint8_t, evicted *C.double, capacity C.uint64_t, final *C.stream_stats_ws_snapshot) C.int32_t {
	return guarded(func() error {
		n := uint64(length)
		if n > 0 && values == nil {
			return windowError{invalid}
		}
		omitted := did == nil && evicted == nil && capacity == 0
		if !omitted && (did == nil || evicted == nil || uint64(capacity) < n) {
			return windowError{invalid}
		}
		input := unsafe.Slice(values, int(n))
		for _, v := range input {
			if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
				return windowError{invalid}
			}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		var flags []C.uint8_t
		var outputs []C.double
		if !omitted {
			flags = unsafe.Slice(did, int(n))
			outputs = unsafe.Slice(evicted, int(n))
		}
		for i, v := range input {
			removed, e := w.add(float64(v))
			if e != nil {
				return e
			}
			if !omitted && removed != nil {
				flags[i] = 1
				outputs[i] = C.double(*removed)
			}
		}
		if final != nil {
			return writeSnapshot(w, final)
		}
		return nil
	})
}

//export stream_stats_ws_remove_oldest
func stream_stats_ws_remove_oldest(raw unsafe.Pointer, out *C.double) C.int32_t {
	return guarded(func() error {
		if out == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		v, e := w.removeOldest()
		*out = C.double(v)
		return e
	})
}

//export stream_stats_ws_remove_value
func stream_stats_ws_remove_value(raw unsafe.Pointer, value C.double) C.int32_t {
	return guarded(func() error {
		w, e := get(raw)
		if e != nil {
			return e
		}
		return w.remove(float64(value))
	})
}

//export stream_stats_ws_snapshot_get
func stream_stats_ws_snapshot_get(raw unsafe.Pointer, out *C.stream_stats_ws_snapshot) C.int32_t {
	return guarded(func() error {
		w, e := get(raw)
		if e != nil {
			return e
		}
		return writeSnapshot(w, out)
	})
}

//export stream_stats_ws_count
func stream_stats_ws_count(raw unsafe.Pointer, out *C.uint64_t) C.int32_t {
	return guarded(func() error {
		if out == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		*out = C.uint64_t(w.length)
		return nil
	})
}

//export stream_stats_ws_sum
func stream_stats_ws_sum(raw unsafe.Pointer, out *C.double) C.int32_t {
	return guarded(func() error {
		if out == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		if w.length == 0 {
			*out = 0
		} else {
			*out = C.double(w.sums[w.root])
		}
		return nil
	})
}

//export stream_stats_ws_min
func stream_stats_ws_min(raw unsafe.Pointer, out *C.double) C.int32_t {
	return guarded(func() error {
		if out == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		if w.length == 0 {
			return windowError{empty}
		}
		*out = C.double(w.mins[w.root])
		return nil
	})
}

//export stream_stats_ws_max
func stream_stats_ws_max(raw unsafe.Pointer, out *C.double) C.int32_t {
	return guarded(func() error {
		if out == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		if w.length == 0 {
			return windowError{empty}
		}
		*out = C.double(w.maxes[w.root])
		return nil
	})
}

//export stream_stats_ws_percentile
func stream_stats_ws_percentile(raw unsafe.Pointer, p C.double, out *C.double) C.int32_t {
	return guarded(func() error {
		if out == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		v, e := w.percentile(float64(p))
		*out = C.double(v)
		return e
	})
}

//export stream_stats_ws_percentile_of
func stream_stats_ws_percentile_of(raw unsafe.Pointer, value C.double, out *C.double) C.int32_t {
	return guarded(func() error {
		if out == nil {
			return windowError{invalid}
		}
		w, e := get(raw)
		if e != nil {
			return e
		}
		v, e := w.percentileOf(float64(value))
		*out = C.double(v)
		return e
	})
}

//export stream_stats_ws_validate
func stream_stats_ws_validate(raw unsafe.Pointer) C.int32_t {
	return guarded(func() error {
		w, e := get(raw)
		if e != nil {
			return e
		}
		return w.validate()
	})
}
func main() {}
