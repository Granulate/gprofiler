FROM gcc:8

COPY native.c .

RUN gcc -fno-omit-frame-pointer native.c -o native

# ensure it's built without debug info
RUN file native | grep -zvq "with debug_info"

CMD ["./native"]
