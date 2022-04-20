# see test_java_async_profiler_musl_and_cpu
FROM java:alpine

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
