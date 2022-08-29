FROM gcc:8

COPY native.c .

RUN gcc -DCHANGE_COMM -pthread native.c -o native

CMD ["./native"]
