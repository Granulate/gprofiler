# these need to be defined before any FROM - otherwise, the ARGs expand to empty strings.

# pyspy & rbspy, using the same builder for both pyspy and rbspy since they share build dependencies - rust:latest 1.52.1
ARG RUST_BUILDER_VERSION=@sha256:9c106c1222abe1450f45774273f36246ebf257623ed51280dbc458632d14c9fc
# pyperf - ubuntu 20.04
ARG PYPERF_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93
# perf - ubuntu:16.04
ARG PERF_BUILDER_UBUNTU=@sha256:d7bb0589725587f2f67d0340edb81fd1fcba6c5f38166639cf2a252c939aa30c
# phpspy - ubuntu:20.04
ARG PHPSPY_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93
# dotnet builder - mcr.microsoft.com/dotnet/sdk:6.0-focal
ARG DOTNET_BUILDER=@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22
# async-profiler glibc build
# requires CentOS 7 so the built DSO can be loaded into machines running with old glibc (tested up to centos:6),
# we do make some modifications to the selected versioned symbols so that we don't use anything from >2.12 (what centos:6
# has)
ARG AP_BUILDER_CENTOS=@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e
# async-profiler musl build - alpine 3.14.2
ARG AP_BUILDER_ALPINE=@sha256:69704ef328d05a9f806b6b8502915e6a0a4faa4d72018dc42343f511490daf8a
# burn - golang:1.16.3
ARG BURN_BUILDER_GOLANG=@sha256:f7d3519759ba6988a2b73b5874b17c5958ac7d0aa48a8b1d84d66ef25fa345f1
# gprofiler - ubuntu 20.04
ARG GPROFILER_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93
# node-package-builder-musl alpine
ARG NODE_PACKAGE_BUILDER_MUSL=@sha256:69704ef328d05a9f806b6b8502915e6a0a4faa4d72018dc42343f511490daf8a
# node-package-builder-glibc - centos/devtoolset-7-toolchain-centos7:latest
ARG NODE_PACKAGE_BUILDER_GLIBC=@sha256:24d4c230cb1fe8e68cefe068458f52f69a1915dd6f6c3ad18aa37c2b8fa3e4e1

# pyspy & rbspy builder base
FROM rust${RUST_BUILDER_VERSION} AS pyspy-rbspy-builder-common
WORKDIR /tmp

COPY scripts/prepare_machine-unknown-linux-musl.sh .
RUN ./prepare_machine-unknown-linux-musl.sh

# pyspy
FROM pyspy-rbspy-builder-common AS pyspy-builder
WORKDIR /tmp

COPY scripts/pyspy_build.sh .
COPY scripts/pyspy_commit.txt .
COPY scripts/pyspy_tag.txt .
RUN ./pyspy_build.sh
RUN mv "/tmp/py-spy/target/$(uname -m)-unknown-linux-musl/release/py-spy" /tmp/py-spy/py-spy

# rbspy
FROM pyspy-rbspy-builder-common AS rbspy-builder
WORKDIR /tmp

COPY scripts/rbspy_build.sh .
RUN ./rbspy_build.sh
RUN mv "/tmp/rbspy/target/$(uname -m)-unknown-linux-musl/release/rbspy" /tmp/rbspy/rbspy

# dotnet-trace
FROM mcr.microsoft.com/dotnet/sdk${DOTNET_BUILDER} as dotnet-builder
WORKDIR /tmp
RUN apt-get update && \
  dotnet tool install --global dotnet-trace

RUN cp -r "$HOME/.dotnet" "/tmp/dotnet"
COPY scripts/dotnet_prepare_dependencies.sh .
RUN ./dotnet_prepare_dependencies.sh

# perf
FROM ubuntu${PERF_BUILDER_UBUNTU} AS perf-builder
WORKDIR /tmp

COPY scripts/perf_env.sh .
RUN ./perf_env.sh

COPY scripts/libunwind_build.sh .
RUN ./libunwind_build.sh

COPY scripts/perf_build.sh .
RUN ./perf_build.sh

# pyperf (bcc)
FROM ubuntu${PYPERF_BUILDER_UBUNTU} AS bcc-builder-base

# not cleaning apt lists here - they are used by subsequent layers that base
# on bcc-builder-base.
# hadolint ignore=DL3009
RUN apt-get update && \
  apt-get install -y --no-install-recommends \
    git \
    ca-certificates \
    && \
  if [ "$(uname -m)" != "aarch64" ]; then \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      curl \
      build-essential \
      iperf llvm-9-dev \
      libclang-9-dev \
      cmake \
      python3 \
      flex \
      libfl-dev \
      bison \
      libelf-dev \
      libz-dev \
      liblzma-dev; \
  fi

# bcc helpers
FROM bcc-builder-base AS bcc-helpers
WORKDIR /tmp

RUN apt-get install -y --no-install-recommends \
  clang-10 \
  llvm-10

COPY --from=perf-builder /bpftool /bpftool

COPY scripts/bcc_helpers_build.sh .
RUN ./bcc_helpers_build.sh

FROM bcc-builder-base AS bcc-builder
WORKDIR /tmp

COPY ./scripts/libunwind_build.sh .
RUN if [ "$(uname -m)" = "aarch64" ]; then \
      exit 0; \
    fi && \
    ./libunwind_build.sh

WORKDIR /bcc

COPY ./scripts/pyperf_build.sh .
RUN ./pyperf_build.sh

# phpspy
FROM ubuntu${PHPSPY_BUILDER_UBUNTU} AS phpspy-builder
WORKDIR /tmp
COPY scripts/phpspy_env.sh .
RUN ./phpspy_env.sh
COPY scripts/phpspy_build.sh .
RUN ./phpspy_build.sh

# async-profiler glibc
FROM centos${AP_BUILDER_CENTOS} AS async-profiler-builder-glibc
WORKDIR /tmp
COPY scripts/async_profiler_env_glibc.sh .
RUN ./async_profiler_env_glibc.sh
COPY scripts/async_profiler_build_shared.sh .
COPY scripts/async_profiler_build_glibc.sh .
RUN ./async_profiler_build_shared.sh /tmp/async_profiler_build_glibc.sh

# async-profiler musl
FROM alpine${AP_BUILDER_ALPINE} AS async-profiler-builder-musl
WORKDIR /tmp
COPY scripts/async_profiler_env_musl.sh .
RUN ./async_profiler_env_musl.sh
COPY scripts/async_profiler_build_shared.sh .
COPY scripts/async_profiler_build_musl.sh .
RUN ./async_profiler_build_shared.sh /tmp/async_profiler_build_musl.sh

# node-package-builder-musl
FROM alpine${NODE_PACKAGE_BUILDER_MUSL} AS node-package-builder-musl
WORKDIR /tmp
COPY scripts/node_builder_musl_env.sh .
RUN ./node_builder_musl_env.sh
COPY scripts/build_node_package.sh .
RUN ./build_node_package.sh

# node-package-builder-glibc
FROM centos/devtoolset-7-toolchain-centos7${NODE_PACKAGE_BUILDER_GLIBC} AS node-package-builder-glibc
USER 0
WORKDIR /tmp
COPY scripts/node_builder_glibc_env.sh .
RUN ./node_builder_glibc_env.sh
COPY scripts/build_node_package.sh .
RUN ./build_node_package.sh
# needed for hadolint
USER 1001

# burn
FROM golang${BURN_BUILDER_GOLANG} AS burn-builder
WORKDIR /tmp
COPY scripts/burn_build.sh .
RUN ./burn_build.sh

# the gProfiler image itself, at last.
FROM ubuntu${GPROFILER_BUILDER_UBUNTU}
WORKDIR /app

# for Aarch64 - it has no .whl file for psutil - so it's trying to build from source.
RUN set -e; \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install --no-install-recommends -y python3-pip && \
    apt-get install --no-install-recommends -y libicu66 && \
    if [ "$(uname -m)" = "aarch64" ]; then \
      apt-get install -y --no-install-recommends build-essential python3.8-dev; \
    fi && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY --from=bcc-builder /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
COPY --from=bcc-builder /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
COPY --from=bcc-builder /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
COPY --from=bcc-builder /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/
COPY --from=bcc-helpers /bpf_get_fs_offset/get_fs_offset gprofiler/resources/python/pyperf/
COPY --from=bcc-helpers /bpf_get_stack_offset/get_stack_offset gprofiler/resources/python/pyperf/

COPY --from=pyspy-builder /tmp/py-spy/py-spy gprofiler/resources/python/py-spy

COPY --from=perf-builder /perf gprofiler/resources/perf

COPY --from=phpspy-builder /tmp/phpspy/phpspy gprofiler/resources/php/phpspy
COPY --from=phpspy-builder /tmp/binutils/binutils-2.25/bin/bin/objdump gprofiler/resources/php/objdump
COPY --from=phpspy-builder /tmp/binutils/binutils-2.25/bin/bin/strings gprofiler/resources/php/strings

COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/jattach gprofiler/resources/java/jattach
COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/async-profiler-version gprofiler/resources/java/async-profiler-version
COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/libasyncProfiler.so gprofiler/resources/java/glibc/libasyncProfiler.so
COPY --from=async-profiler-builder-musl /tmp/async-profiler/build/libasyncProfiler.so gprofiler/resources/java/musl/libasyncProfiler.so
COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/fdtransfer gprofiler/resources/java/fdtransfer
COPY --from=node-package-builder-musl /tmp/module_build gprofiler/resources/node/module/musl
COPY --from=node-package-builder-glibc /tmp/module_build gprofiler/resources/node/module/glibc

COPY --from=rbspy-builder /tmp/rbspy/rbspy gprofiler/resources/ruby/rbspy

ENV DOTNET_ROOT=/app/gprofiler/resources/dotnet
COPY --from=dotnet-builder /usr/share/dotnet/host gprofiler/resources/dotnet/host
COPY --from=dotnet-builder /tmp/dotnet/deps gprofiler/resources/dotnet/shared/Microsoft.NETCore.App/6.0.7
COPY --from=dotnet-builder /tmp/dotnet/tools gprofiler/resources/dotnet/tools

COPY --from=burn-builder /tmp/burn/burn gprofiler/resources/burn

# we want the latest pip
# hadolint ignore=DL3013
RUN pip3 install --upgrade --no-cache-dir pip

# done separately from the 'pip3 install -e' below; so we don't reinstall all dependencies on each
# code change.
COPY requirements.txt ./
COPY granulate-utils/setup.py granulate-utils/requirements.txt granulate-utils/README.md granulate-utils/
COPY granulate-utils/granulate_utils granulate-utils/granulate_utils
RUN pip3 install --no-cache-dir -r requirements.txt

COPY LICENSE.md MANIFEST.in README.md setup.py  ./
COPY gprofiler gprofiler
RUN pip3 install --no-cache-dir -e .

# lets gProfiler know it is running in a container
ENV GPROFILER_IN_CONTAINER=1

ENTRYPOINT [ "python3", "-m", "gprofiler" ]
