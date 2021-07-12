FROM node:10

WORKDIR /app
ADD fibonacci.js /app

CMD ["node", "--perf-prof", "--interpreted-frames-native-stack", "fibonacci.js"]
