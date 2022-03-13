# openjdk:8u275
FROM openjdk@sha256:b253b93dc528967eff64ade00a9079dc464fb75b9d27a7a578c8006ca0645de8

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
