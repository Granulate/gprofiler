FROM gcc:8

COPY native.c .

RUN gcc -g -fomit-frame-pointer native.c -o native

# ensure it's built with debug info
RUN file native | grep -q "with debug_info"

CMD ["./native"]
