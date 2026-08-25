SCALAC ?= scalac
JAVA ?= java
JAR ?= jar
SCALA_HOME ?= /opt/homebrew/opt/scala/libexec
SCALA_LIBS := $(SCALA_HOME)/maven2/org/scala-lang/scala3-library_3/3.8.4/scala3-library_3-3.8.4.jar:$(SCALA_HOME)/maven2/org/scala-lang/scala-library/3.8.4/scala-library-3.8.4.jar
SCALA3_JAR := $(SCALA_HOME)/maven2/org/scala-lang/scala3-library_3/3.8.4/scala3-library_3-3.8.4.jar
SCALA2_JAR := $(SCALA_HOME)/maven2/org/scala-lang/scala-library/3.8.4/scala-library-3.8.4.jar
SOURCES := $(wildcard src/streamstats/scala/*.scala)

.PHONY: all test clean
all: build/stream-stats-scala.jar

build/classes/streamstats/scala/Worker.class: $(SOURCES)
	mkdir -p build/classes
	COURSIER_CACHE=/tmp/stream-stats-coursier $(SCALAC) -d build/classes $(SOURCES)

build/stream-stats-scala.jar: build/classes/streamstats/scala/Worker.class
	mkdir -p build/assembly
	cp -R build/classes/. build/assembly/
	cd build/assembly && $(JAR) --extract --file $(SCALA3_JAR)
	cd build/assembly && $(JAR) --extract --file $(SCALA2_JAR)
	$(JAR) --create --file $@ --main-class streamstats.scala.Worker -C build/assembly .

test: all
	$(JAVA) -cp build/stream-stats-scala.jar streamstats.scala.NativeTest

clean:
	rm -rf build
