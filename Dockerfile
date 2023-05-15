FROM alpine as gprofiler
ARG ARCH
ARG EXE_PATH=build/${ARCH}/gprofiler
ENV GPROFILER_IN_CONTAINER=1
COPY ${EXE_PATH} /gprofiler
RUN chmod +x /gprofiler
ENTRYPOINT ["/gprofiler"]