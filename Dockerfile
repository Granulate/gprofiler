# py-spy
# rust:latest 1.52.1
FROM rust@sha256:5f3bbf6200c057c4934deac814224e0038baa018c76aa54dfb84dd734315dad4 AS pyspy-builder

COPY scripts/pyspy_env.sh .
RUN ./pyspy_env.sh

COPY scripts/pyspy_build.sh .
RUN ./pyspy_build.sh

# perf
# ubuntu:16.04
FROM ubuntu@sha256:d7bb0589725587f2f67d0340edb81fd1fcba6c5f38166639cf2a252c939aa30c AS perf-builder

COPY scripts/perf_env.sh .
RUN ./perf_env.sh

COPY scripts/perf_build.sh .
RUN ./perf_build.sh

# pyperf (bcc)
# ubuntu 20.04
FROM ubuntu@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93 AS bcc-builder

RUN apt-get update

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y git build-essential iperf llvm-9-dev libclang-9-dev \
  cmake python3 flex bison libelf-dev libz-dev

WORKDIR /bcc

COPY ./scripts/pyperf_build.sh .
RUN ./pyperf_build.sh

# phpspy
# ubuntu:20.04
FROM ubuntu@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93 as phpspy-builder
RUN apt update && apt install -y git wget make gcc
COPY scripts/phpspy_build.sh .
RUN ./phpspy_build.sh

# async-profiler
# centos:7
FROM centos@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e AS async-profiler-builder
COPY scripts/async_profiler_env.sh .
RUN ./async_profiler_env.sh
COPY scripts/async_profiler_build.sh .
RUN ./async_profiler_build.sh


# the gProfiler image itself, at last.
# ubuntu 20.04
FROM ubuntu@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93

WORKDIR /app

# kmod - for modprobe kheaders if it's available
RUN apt-get update && apt-get install -y curl python3-pip kmod

COPY --from=bcc-builder /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
COPY --from=bcc-builder /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
COPY --from=bcc-builder /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
COPY --from=bcc-builder /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/

COPY --from=pyspy-builder /py-spy/target/x86_64-unknown-linux-musl/release/py-spy gprofiler/resources/python/py-spy
COPY --from=perf-builder /perf gprofiler/resources/perf

COPY --from=phpspy-builder /phpspy/phpspy gprofiler/resources/php/phpspy
COPY --from=phpspy-builder /binutils/binutils-2.25/bin/bin/objdump gprofiler/resources/php/objdump

RUN mkdir -p gprofiler/resources/java
COPY --from=async-profiler-builder /async-profiler/async-profiler-2.0-linux-x64.tar.gz /tmp
RUN tar -xzf /tmp/async-profiler-2.0-linux-x64.tar.gz -C gprofiler/resources/java --strip-components=2 async-profiler-2.0-linux-x64/build && rm /tmp/async-profiler-2.0-linux-x64.tar.gz

COPY scripts/build.sh scripts/build.sh
RUN ./scripts/build.sh

COPY requirements.txt requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY LICENSE.md MANIFEST.in README.md setup.py ./
COPY gprofiler gprofiler
RUN python3 setup.py install

# lets gProfiler know it is running in a container
ENV GPROFILER_IN_CONTAINER=1

ENTRYPOINT [ "python3", "-m", "gprofiler" ]
