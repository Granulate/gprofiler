ARG NODE_IMAGE_TAG
ARG NODE_IMAGE=node
FROM ${NODE_IMAGE}${NODE_IMAGE_TAG}
USER 0
ARG NODE_RUNTIME_FLAGS

# /tmp so node has permissions to write its jitdump file
WORKDIR /tmp

RUN mkdir /app
ADD fibonacci.js /app

ENV NODE_RUNTIME_FLAGS ${NODE_RUNTIME_FLAGS}

CMD exec node $NODE_RUNTIME_FLAGS /app/fibonacci.js
