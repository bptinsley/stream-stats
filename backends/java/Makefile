JAVAC ?= javac
JAR ?= jar
JAVA ?= java

.PHONY: all test clean
all: build/stream-stats-java.jar

build/stream-stats-java.jar: src/streamstats/WindowStatistics.java src/streamstats/Worker.java
	mkdir -p build/classes
	$(JAVAC) -d build/classes $^
	$(JAR) --create --file $@ --main-class streamstats.Worker -C build/classes .

test: all
	$(JAVAC) -cp build/classes -d build/classes src/streamstats/NativeTest.java
	$(JAVA) -cp build/classes streamstats.NativeTest

clean:
	rm -rf build
