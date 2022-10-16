FROM golang:1.18.3

WORKDIR /app
ADD fibonacci.go /app
ENV GOCACHE=/tmp
RUN go build -ldflags "-linkmode external" fibonacci.go

CMD ["./fibonacci"]
