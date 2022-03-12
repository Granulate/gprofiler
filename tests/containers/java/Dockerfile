ARG JAVA_BASE_IMAGE=openjdk:8u275
FROM ${JAVA_BASE_IMAGE}

WORKDIR /app
ADD Fibonacci.java /app
ADD MANIFEST.MF /app
RUN javac Fibonacci.java
RUN jar cvmf MANIFEST.MF Fibonacci.jar *.class

CMD ["sh", "-c", "java -jar Fibonacci.jar; sleep 10000"]
