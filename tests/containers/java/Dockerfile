# openjdk:8u275
ARG JAVA_BASE_IMAGE=openjdk@sha256:86e863cc57215cfb181bd319736d0baf625fe8f150577f9eb58bd937f5452cb8
FROM ${JAVA_BASE_IMAGE}

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class
# create a java binary with different basename, see test_java_different_basename
RUN cp `which java` `which java`-notjava

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
