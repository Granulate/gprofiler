# rust:latest 1.52.1
# using the same builder for both pyspy and rbspy since they share build dependencies
FROM rust@sha256:9c106c1222abe1450f45774273f36246ebf257623ed51280dbc458632d14c9fc AS pyspy-rbspy-builder-common

COPY scripts/prepare_x86_64-unknown-linux-musl.sh .
RUN ./prepare_x86_64-unknown-linux-musl.sh

FROM pyspy-rbspy-builder-common AS pyspy-builder
COPY scripts/pyspy_build.sh .
RUN ./pyspy_build.sh

# rbspy
FROM pyspy-rbspy-builder-common AS rbspy-builder
COPY scripts/rbspy_build.sh .
RUN ./rbspy_build.sh

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
# centos:6
FROM centos@sha256:dec8f471302de43f4cfcf82f56d99a5227b5ea1aa6d02fa56344986e1f4610e7 AS async-profiler-builder
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

RUN mkdir -p gprofiler/resources/ruby
COPY --from=rbspy-builder /rbspy/target/x86_64-unknown-linux-musl/release/rbspy gprofiler/resources/ruby/rbspy

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
