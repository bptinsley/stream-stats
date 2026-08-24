# Java backend

Long-lived worker process packaged as a JAR. The Python adapter owns one worker
per backend instance and uses a versioned framed protocol, avoiding a JVM start
for every sample.
