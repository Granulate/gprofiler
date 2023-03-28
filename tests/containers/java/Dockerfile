# openjdk:8u332
ARG JAVA_BASE_IMAGE=openjdk@sha256:b5dfe8c78614fb242b7b3d4f16ed375cd03023e8280d5eeb01fec75ba924f864
FROM ${JAVA_BASE_IMAGE}

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class
# create a java binary with different basename, see test_java_different_basename
RUN cp `which java` `which java`-notjava

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
