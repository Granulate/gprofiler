FROM golang:1.18.3

WORKDIR /app
ADD fibonacci.go /app
ENV GOCACHE=/tmp
RUN go build -ldflags "-linkmode external" fibonacci.go
RUN go build -ldflags "-linkmode external -w -s" -o fibonacci-stripped  fibonacci.go

CMD ["./fibonacci"]
