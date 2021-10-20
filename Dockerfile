# these need to be defined before any FROM - otherwise, the ARGs expand to empty strings.

# pyspy & rbspy, using the same builder for both pyspy and rbspy since they share build dependencies - rust:latest 1.52.1
ARG RUST_BUILDER_VERSION=@sha256:9c106c1222abe1450f45774273f36246ebf257623ed51280dbc458632d14c9fc
# pyperf - ubuntu 20.04
ARG PYPERF_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93
# perf - ubuntu:16.04
ARG PERF_BUILDER_UBUNTU=@sha256:d7bb0589725587f2f67d0340edb81fd1fcba6c5f38166639cf2a252c939aa30c
# phpspy - ubuntu:20.04
ARG PHPSPY_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93
# async-profiler, requires CentOS 6, so the built DSO can be loaded into machines running with old glibc - centos:6
ARG AP_BUILDER_CENTOS=@sha256:dec8f471302de43f4cfcf82f56d99a5227b5ea1aa6d02fa56344986e1f4610e7
# burn - golang:1.16.3
ARG BURN_BUILDER_GOLANG=@sha256:f7d3519759ba6988a2b73b5874b17c5958ac7d0aa48a8b1d84d66ef25fa345f1
# gprofiler - ubuntu 20.04
ARG GPROFILER_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93

# pyspy & rbspy builder base
FROM rust${RUST_BUILDER_VERSION} AS pyspy-rbspy-builder-common

COPY scripts/prepare_machine-unknown-linux-musl.sh .
RUN ./prepare_machine-unknown-linux-musl.sh

# pyspy
FROM pyspy-rbspy-builder-common AS pyspy-builder
COPY scripts/pyspy_build.sh .
RUN ./pyspy_build.sh
RUN mv /py-spy/target/$(uname -m)-unknown-linux-musl/release/py-spy /py-spy/py-spy

# rbspy
FROM pyspy-rbspy-builder-common AS rbspy-builder
COPY scripts/rbspy_build.sh .
RUN ./rbspy_build.sh
RUN mv /rbspy/target/$(uname -m)-unknown-linux-musl/release/rbspy /rbspy/rbspy

# perf
FROM ubuntu${PERF_BUILDER_UBUNTU} AS perf-builder

COPY scripts/perf_env.sh .
RUN ./perf_env.sh

COPY scripts/libunwind_build.sh .
RUN ./libunwind_build.sh

COPY scripts/perf_build.sh .
RUN ./perf_build.sh

# pyperf (bcc)
FROM ubuntu${PYPERF_BUILDER_UBUNTU} AS bcc-builder

RUN apt-get update && apt-get install -y git && if [ $(uname -m) = "aarch64" ]; then exit 0; fi; DEBIAN_FRONTEND=noninteractive apt-get install -y \
  curl build-essential iperf llvm-9-dev libclang-9-dev cmake python3 flex bison libelf-dev libz-dev liblzma-dev

COPY ./scripts/libunwind_build.sh .
RUN if [ $(uname -m) = "aarch64" ]; then exit 0; fi; ./libunwind_build.sh

WORKDIR /bcc

COPY ./scripts/pyperf_build.sh .
RUN ./pyperf_build.sh

# phpspy
FROM ubuntu${PHPSPY_BUILDER_UBUNTU} AS phpspy-builder
RUN if [ $(uname -m) = "aarch64" ]; then exit 0; fi; apt update && apt install -y git wget make gcc
COPY scripts/phpspy_build.sh .
RUN ./phpspy_build.sh

# async-profiler
FROM centos${AP_BUILDER_CENTOS} AS async-profiler-builder
COPY scripts/async_profiler_env.sh .
RUN ./async_profiler_env.sh
COPY scripts/async_profiler_build.sh .
RUN ./async_profiler_build.sh

# burn
FROM golang${BURN_BUILDER_GOLANG} AS burn-builder

COPY scripts/burn_build.sh .
RUN ./burn_build.sh


# the gProfiler image itself, at last.
FROM ubuntu${GPROFILER_BUILDER_UBUNTU}

WORKDIR /app

# kmod - for modprobe kheaders if it's available
RUN apt-get update && apt-get install --no-install-recommends -y curl python3-pip kmod

# Aarch64 has no .whl file for psutil - so it's trying to build from source.
RUN if [ $(uname -m) = "aarch64" ]; then apt-get install -y build-essential python3.8-dev; fi

COPY --from=bcc-builder /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
COPY --from=bcc-builder /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
COPY --from=bcc-builder /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
COPY --from=bcc-builder /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/

COPY --from=pyspy-builder /py-spy/py-spy gprofiler/resources/python/py-spy

COPY --from=perf-builder /perf gprofiler/resources/perf

COPY --from=phpspy-builder /phpspy/phpspy gprofiler/resources/php/phpspy
COPY --from=phpspy-builder /binutils/binutils-2.25/bin/bin/objdump gprofiler/resources/php/objdump
COPY --from=phpspy-builder /binutils/binutils-2.25/bin/bin/strings gprofiler/resources/php/strings

COPY --from=async-profiler-builder /async-profiler/build/jattach gprofiler/resources/java/jattach
COPY --from=async-profiler-builder /async-profiler/build/async-profiler-version gprofiler/resources/java/async-profiler-version
COPY --from=async-profiler-builder /async-profiler/build/libasyncProfiler.so gprofiler/resources/java/libasyncProfiler.so
COPY --from=async-profiler-builder /async-profiler/build/fdtransfer gprofiler/resources/java/fdtransfer

COPY --from=rbspy-builder /rbspy/rbspy gprofiler/resources/ruby/rbspy

COPY --from=burn-builder /go/burn/burn gprofiler/resources/burn

# done separately from the 'pip3 install -e' below; so we don't reinstall all dependencies on each
# code change.
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY LICENSE.md MANIFEST.in README.md setup.py  ./
COPY gprofiler gprofiler
RUN pip3 install --no-cache-dir -e .

# lets gProfiler know it is running in a container
ENV GPROFILER_IN_CONTAINER=1

ENTRYPOINT [ "python3", "-m", "gprofiler" ]
