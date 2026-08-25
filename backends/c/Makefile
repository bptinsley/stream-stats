CC ?= cc
CFLAGS ?= -O3 -std=c11 -fPIC -fvisibility=hidden -fno-fast-math -ffp-contract=off
CPPFLAGS ?= -Iinclude
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Darwin)
  LIBRARY := build/libstream_stats_c.dylib
  LDFLAGS := -dynamiclib
else
  LIBRARY := build/libstream_stats_c.so
  LDFLAGS := -shared
endif

.PHONY: all clean test sanitize

all: $(LIBRARY)

$(LIBRARY): src/stream_stats_window.c include/stream_stats_window.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) src/stream_stats_window.c -o $@ -lm

clean:
	rm -f build/libstream_stats_c.dylib build/libstream_stats_c.so

test: all
	$(CC) $(CPPFLAGS) -O1 -g -std=c11 tests/test_native.c src/stream_stats_window.c -o build/test_native -lm
	./build/test_native

sanitize:
	mkdir -p build
	$(CC) $(CPPFLAGS) -O1 -g -std=c11 -fsanitize=address,undefined -fno-omit-frame-pointer tests/test_native.c src/stream_stats_window.c -o build/test_sanitize -lm
	./build/test_sanitize
