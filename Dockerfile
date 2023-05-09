FROM alpine as gprofiler
ARG ARCH
ENV GPROFILER_IN_CONTAINER=1
COPY build/${ARCH}/gprofiler /gprofiler
ENTRYPOINT ["/gprofiler"]