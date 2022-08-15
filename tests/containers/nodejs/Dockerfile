# node:10.24.1
FROM node@sha256:59531d2835edd5161c8f9512f9e095b1836f7a1fcb0ab73e005ec46047384911

# /tmp so node has permissions to write its jitdump file
WORKDIR /tmp

RUN mkdir /app
ADD fibonacci.js /app

CMD ["node", "--perf-prof", "--interpreted-frames-native-stack", "/app/fibonacci.js"]
