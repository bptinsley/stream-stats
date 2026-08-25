PYTHON ?= .venv/bin/python
JAVA_HOME ?= /opt/homebrew/opt/openjdk
SCALA_HOME ?= /opt/homebrew/opt/scala/libexec
GO_CACHE ?= /tmp/stream-stats-go-cache
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  GO_LIBRARY := build/libstream_stats_go.dylib
else
  GO_LIBRARY := build/libstream_stats_go.so
endif

.PHONY: build-cython build-backends test-backends test benchmark-report

build-cython:
	STREAM_STATS_BUILD_CYTHON=1 $(PYTHON) setup.py build_ext --inplace

build-backends: build-cython
	$(MAKE) -C backends/c all
	cmake -S backends/cpp -B backends/cpp/build -DCMAKE_BUILD_TYPE=Release
	cmake --build backends/cpp/build -j 4
	cd backends/rust && cargo build --release
	mkdir -p backends/go/build $(GO_CACHE)
	cd backends/go && GOCACHE=$(GO_CACHE) go build -buildmode=c-shared -o $(GO_LIBRARY) .
	$(MAKE) -C backends/java JAVAC=$(JAVA_HOME)/bin/javac JAR=$(JAVA_HOME)/bin/jar JAVA=$(JAVA_HOME)/bin/java all
	$(MAKE) -C backends/scala SCALAC=/opt/homebrew/bin/scalac JAVA=$(JAVA_HOME)/bin/java JAR=$(JAVA_HOME)/bin/jar SCALA_HOME=$(SCALA_HOME) all

test-backends: build-backends
	$(MAKE) -C backends/c test
	ctest --test-dir backends/cpp/build --output-on-failure
	cd backends/rust && cargo test && cargo clippy --all-targets -- -D warnings
	cd backends/go && GOCACHE=$(GO_CACHE) go test ./...
	$(MAKE) -C backends/java JAVAC=$(JAVA_HOME)/bin/javac JAR=$(JAVA_HOME)/bin/jar JAVA=$(JAVA_HOME)/bin/java test
	$(MAKE) -C backends/scala SCALAC=/opt/homebrew/bin/scalac JAVA=$(JAVA_HOME)/bin/java JAR=$(JAVA_HOME)/bin/jar SCALA_HOME=$(SCALA_HOME) test

test: test-backends
	$(PYTHON) -m pytest -q

benchmark-report: build-backends
	$(PYTHON) benchmarks/benchmark_operations.py --updates 100000 --repeats 3
