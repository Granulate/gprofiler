# parts are copied from Dockerfile

# these need to be defined before any FROM - otherwise, the ARGs expand to empty strings.

# pyspy & rbspy, using the same builder for both pyspy and rbspy since they share build dependencies - rust:latest 1.52.1
ARG RUST_BUILDER_VERSION=@sha256:9c106c1222abe1450f45774273f36246ebf257623ed51280dbc458632d14c9fc
# perf - ubuntu:16.04
ARG PERF_BUILDER_UBUNTU=@sha256:d7bb0589725587f2f67d0340edb81fd1fcba6c5f38166639cf2a252c939aa30c
# phpspy - ubuntu:20.04
ARG PHPSPY_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93
# async-profiler glibc - centos:6
# requires CentOS 6 so the built DSO can be loaded into machines running with old glibc - centos:6
ARG AP_BUILDER_CENTOS=@sha256:dec8f471302de43f4cfcf82f56d99a5227b5ea1aa6d02fa56344986e1f4610e7
# async-profiler musl build
ARG AP_BUILDER_ALPINE=@sha256:69704ef328d05a9f806b6b8502915e6a0a4faa4d72018dc42343f511490daf8a
# burn - golang:1.16.3
ARG BURN_BUILDER_GOLANG=@sha256:f7d3519759ba6988a2b73b5874b17c5958ac7d0aa48a8b1d84d66ef25fa345f1
# bcc & gprofiler - centos:7
# CentOS 7 image is used to grab an old version of `glibc` during `pyinstaller` bundling.
# this will allow the executable to run on older versions of the kernel, eventually leading to the executable running on a wider range of machines.
ARG GPROFILER_BUILDER=@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e
# pyperf - ubuntu 20.04
ARG PYPERF_BUILDER_UBUNTU=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93

# pyspy & rbspy builder base
FROM rust${RUST_BUILDER_VERSION} AS pyspy-rbspy-builder-common

COPY scripts/prepare_machine-unknown-linux-musl.sh .
RUN ./prepare_machine-unknown-linux-musl.sh

# py-spy
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

# phpspy
FROM ubuntu${PHPSPY_BUILDER_UBUNTU} as phpspy-builder
RUN if [ $(uname -m) = "aarch64" ]; then exit 0; fi; apt update && apt install -y git wget make gcc
COPY scripts/phpspy_build.sh .
RUN ./phpspy_build.sh

# async-profiler glibc
FROM centos${AP_BUILDER_CENTOS} AS async-profiler-builder-glibc
COPY scripts/async_profiler_env_glibc.sh .
RUN ./async_profiler_env_glibc.sh
COPY scripts/async_profiler_build_shared.sh .
COPY scripts/async_profiler_build_glibc.sh .
RUN ./async_profiler_build_shared.sh /async_profiler_build_glibc.sh

# async-profiler musl
FROM alpine${AP_BUILDER_ALPINE} AS async-profiler-builder-musl
COPY scripts/async_profiler_env_musl.sh .
RUN ./async_profiler_env_musl.sh
COPY scripts/async_profiler_build_shared.sh .
COPY scripts/async_profiler_build_musl.sh .
RUN ./async_profiler_build_shared.sh /async_profiler_build_musl.sh

FROM golang${BURN_BUILDER_GOLANG} AS burn-builder

COPY scripts/burn_build.sh .
RUN ./burn_build.sh

# bcc helpers
# built on newer Ubuntu because they require new clang (newer than available in GPROFILER_BUILDER's CentOS 7)
# these are only relevant for modern kernels, so there's no real reason to build them on CentOS 7 anyway.
FROM ubuntu${PYPERF_BUILDER_UBUNTU} AS bcc-helpers

RUN apt-get update && apt install -y \
    clang-10 \
    libelf-dev \
    make \
    build-essential \
    llvm \
    git

COPY --from=perf-builder /bpftool /bpftool

COPY scripts/bcc_helpers_build.sh .
RUN ./bcc_helpers_build.sh


# bcc & gprofiler
FROM centos${GPROFILER_BUILDER} AS build-stage

# bcc part
# TODO: copied from the main Dockerfile... but modified a lot. we'd want to share it some day.

RUN yum install -y git

RUN yum install -y \
    curl \
    cmake \
    patch \
    python3 \
    flex \
    bison \
    zlib-devel.x86_64 \
    xz-devel \
    ncurses-devel \
    elfutils-libelf-devel

RUN yum install -y centos-release-scl-rh
# mostly taken from https://github.com/iovisor/bcc/blob/master/INSTALL.md#install-and-compile-llvm
# on x86_64, the package is named llvm-toolset-7. on aarch64, it is named llvm-toolset-7.0...
RUN if [ $(uname -m) = "aarch64" ]; then v="7.0"; else v="7"; fi; yum install -y devtoolset-8 \
    llvm-toolset-$v \
    llvm-toolset-$v-llvm-devel \
    llvm-toolset-$v-llvm-static \
    llvm-toolset-$v-clang-devel \
    devtoolset-8-elfutils-libelf-devel

COPY ./scripts/libunwind_build.sh .
RUN ./libunwind_build.sh

WORKDIR /bcc

COPY ./scripts/pyperf_build.sh .
RUN source scl_source enable devtoolset-8 llvm-toolset-7.0 && source ./pyperf_build.sh


# gProfiler part

WORKDIR /app

RUN yum install -y epel-release
RUN yum install -y gcc python3 curl python3-pip patchelf python3-devel upx
# needed for aarch64
RUN if [ $(uname -m) = "aarch64" ]; then yum install -y glibc-static zlib-devel.aarch64; fi
# needed for aarch64, scons & wheel are needed to build staticx
RUN if [ $(uname -m) = "aarch64" ]; then python3 -m pip install 'wheel==0.37.0' 'scons==4.2.0'; fi

RUN python3 -m pip install --upgrade pip

COPY requirements.txt requirements.txt
COPY granulate-utils/setup.py granulate-utils/requirements.txt granulate-utils/README.md granulate-utils/
COPY granulate-utils/granulate_utils granulate-utils/granulate_utils
RUN python3 -m pip install -r requirements.txt

COPY exe-requirements.txt exe-requirements.txt
RUN python3 -m pip install -r exe-requirements.txt

# copy PyPerf and stuff
RUN mkdir -p gprofiler/resources/ruby
RUN mkdir -p gprofiler/resources/python/pyperf
RUN cp /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
RUN cp /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
RUN cp -r /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
RUN cp /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/
COPY --from=bcc-helpers /bpf_get_fs_offset/get_fs_offset gprofiler/resources/python/pyperf/
COPY --from=bcc-helpers /bpf_get_stack_offset/get_stack_offset gprofiler/resources/python/pyperf/

COPY --from=pyspy-builder /py-spy/py-spy gprofiler/resources/python/py-spy
COPY --from=rbspy-builder /rbspy/rbspy gprofiler/resources/ruby/rbspy
COPY --from=perf-builder /perf gprofiler/resources/perf

COPY --from=phpspy-builder /phpspy/phpspy gprofiler/resources/php/phpspy
COPY --from=phpspy-builder /binutils/binutils-2.25/bin/bin/objdump gprofiler/resources/php/objdump
COPY --from=phpspy-builder /binutils/binutils-2.25/bin/bin/strings gprofiler/resources/php/strings
# copying from async-profiler-builder as an "old enough" centos.
COPY --from=async-profiler-builder-glibc /usr/bin/awk gprofiler/resources/php/awk
COPY --from=async-profiler-builder-glibc /usr/bin/xargs gprofiler/resources/php/xargs

COPY --from=async-profiler-builder-glibc /async-profiler/build/jattach gprofiler/resources/java/jattach
COPY --from=async-profiler-builder-glibc /async-profiler/build/async-profiler-version gprofiler/resources/java/async-profiler-version
COPY --from=async-profiler-builder-glibc /async-profiler/build/libasyncProfiler.so gprofiler/resources/java/glibc/libasyncProfiler.so
COPY --from=async-profiler-builder-musl /async-profiler/build/libasyncProfiler.so gprofiler/resources/java/musl/libasyncProfiler.so
COPY --from=async-profiler-builder-glibc /async-profiler/build/fdtransfer gprofiler/resources/java/fdtransfer

COPY --from=burn-builder /go/burn/burn gprofiler/resources/burn

COPY gprofiler gprofiler

# run PyInstaller and make sure no 'gprofiler.*' modules are missing.
# see https://pyinstaller.readthedocs.io/en/stable/when-things-go-wrong.html
# from a quick look I didn't see how to tell PyInstaller to exit with an error on this, hence
# this check in the shell.
COPY pyi_build.py pyinstaller.spec ./
RUN pyinstaller pyinstaller.spec \
    && echo \
    && test -f build/pyinstaller/warn-pyinstaller.txt \
    && if grep 'gprofiler\.' build/pyinstaller/warn-pyinstaller.txt ; then echo 'PyInstaller failed to pack gProfiler code! See lines above. Make sure to check for SyntaxError as this is often the reason.'; exit 1; fi;

COPY ./scripts/list_needed_libs.sh ./scripts/list_needed_libs.sh
# staticx packs dynamically linked app with all of their dependencies, it tries to figure out which dynamic libraries are need for its execution
# in some cases, when the application is lazily loading some DSOs, staticx doesn't handle it.
# we use list_needed_libs.sh to list the dynamic dependencies of *all* of our resources,
# and make staticx pack them as well.
# using scl here to get the proper LD_LIBRARY_PATH set
RUN if [ $(uname -m) = "aarch64" ]; then v="7.0"; else v="7"; fi; source scl_source enable devtoolset-8 llvm-toolset-$v && libs=$(./scripts/list_needed_libs.sh) && staticx $libs dist/gprofiler dist/gprofiler

FROM scratch AS export-stage

COPY --from=build-stage /app/dist/gprofiler /gprofiler
