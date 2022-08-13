FROM gcc:8

COPY native.c .

RUN gcc -DTHREAD_COMM -pthread native.c -o native

CMD ["./native"]
