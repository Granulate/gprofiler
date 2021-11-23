FROM openjdk:8u275

WORKDIR /app
ADD Fibonacci.java /app
RUN javac Fibonacci.java

CMD ["sh", "-c", "java Fibonacci; sleep 10000"]
