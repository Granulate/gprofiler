FROM node:10

# /tmp so node has permissions to write its jitdump file
WORKDIR /tmp

RUN mkdir /app
ADD fibonacci.js /app

CMD ["node", "--perf-prof", "--interpreted-frames-native-stack", "/app/fibonacci.js"]
