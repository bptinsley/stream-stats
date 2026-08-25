# Scala backend

Independent Scala 3 primitive-array recycled AVL using the same versioned
worker protocol as Java. Protocol framing is shared; tree code is not. The
build produces a self-contained runnable JAR. Build and test with
`make -C backends/scala test`.
