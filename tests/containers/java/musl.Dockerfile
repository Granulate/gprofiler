# see test_java_async_profiler_musl_and_cpu
FROM openjdk@sha256:d49bf8c44670834d3dade17f8b84d709e7db47f1887f671a0e098bafa9bae49f

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
