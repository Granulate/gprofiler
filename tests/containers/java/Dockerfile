# openjdk:8u275
ARG JAVA_BASE_IMAGE=openjdk@sha256:b253b93dc528967eff64ade00a9079dc464fb75b9d27a7a578c8006ca0645de8
FROM ${JAVA_BASE_IMAGE}

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class
# create a java binary with different basename, see test_java_different_basename
RUN cp `which java` `which java`-notjava

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
