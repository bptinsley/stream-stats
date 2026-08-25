package main

import (
	"math"
	"testing"
)

func TestWindowWrapAndQueries(t *testing.T) {
	w, err := newWindow(3)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []float64{1, 2, 3, 4} {
		if _, err = w.add(value); err != nil {
			t.Fatal(err)
		}
	}
	if w.length != 3 || w.sums[w.root] != 9 {
		t.Fatalf("unexpected state")
	}
	v, err := w.variance()
	if err != nil || math.Abs(v-2.0/3.0) > 1e-12 {
		t.Fatalf("variance %v %v", v, err)
	}
	if p, _ := w.percentile(50); p != 3 {
		t.Fatalf("median %v", p)
	}
}

func TestBatchValidationCanPrecedeMutation(t *testing.T) {
	w, _ := newWindow(2)
	_, _ = w.add(9)
	if _, e := normalize(math.NaN()); e == nil {
		t.Fatal("NaN accepted")
	}
	if w.sums[w.root] != 9 {
		t.Fatal("state changed")
	}
}
