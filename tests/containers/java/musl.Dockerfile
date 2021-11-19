FROM openjdk:8-alpine

WORKDIR /app
ADD Fibonacci.java /app
RUN javac Fibonacci.java

CMD ["sh", "-c", "java Fibonacci; sleep 10000"]
