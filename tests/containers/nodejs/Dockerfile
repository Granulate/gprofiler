FROM node:10

WORKDIR /app
ADD ficonacci.js /app

CMD ["node", "--perf-prof", "--interpreted-frames-native-stack", "fibonacci.js"]
