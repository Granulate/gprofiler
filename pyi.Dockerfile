# parts are copied from Dockerfile

# rust:slim 1.52.1
# using the same builder for both pyspy and rbspy since they share build dependencies
FROM rust@sha256:9c106c1222abe1450f45774273f36246ebf257623ed51280dbc458632d14c9fc AS pyspy-rbspy-builder-common

COPY scripts/prepare_x86_64-unknown-linux-musl.sh .
RUN ./prepare_x86_64-unknown-linux-musl.sh

# py-spy
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

# phpspy
# ubuntu:20.04
FROM ubuntu@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93 as phpspy-builder
RUN apt update && apt install -y git wget make gcc
COPY scripts/phpspy_build.sh .
RUN ./phpspy_build.sh

# async-profiler
FROM centos@sha256:dec8f471302de43f4cfcf82f56d99a5227b5ea1aa6d02fa56344986e1f4610e7 AS async-profiler-builder
COPY scripts/async_profiler_env.sh .
RUN ./async_profiler_env.sh
COPY scripts/async_profiler_build.sh .
RUN ./async_profiler_build.sh


# Centos 7 image is used to grab an old version of `glibc` during `pyinstaller` bundling.
# This will allow the executable to run on older versions of the kernel, eventually leading to the executable running on a wider range of machines.
# centos:7
FROM centos@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e AS build-stage

# bcc part
# TODO: copied from the main Dockerfile... but modified a lot. we'd want to share it some day.

RUN yum install -y \
    git \
    cmake \
    python3 \
    flex \
    bison \
    zlib-devel.x86_64 \
    ncurses-devel \
    elfutils-libelf-devel

WORKDIR /bcc

RUN yum install -y centos-release-scl-rh
# mostly taken from https://github.com/iovisor/bcc/blob/master/INSTALL.md#install-and-compile-llvm
RUN yum install -y devtoolset-8 \
    llvm-toolset-7 \
    llvm-toolset-7-llvm-devel \
    llvm-toolset-7-llvm-static \
    llvm-toolset-7-clang-devel \
    devtoolset-8-elfutils-libelf-devel

COPY ./scripts/pyperf_build.sh .
RUN source scl_source enable devtoolset-8 llvm-toolset-7 && source ./pyperf_build.sh


# gProfiler part

WORKDIR /app

RUN yum update -y && yum install -y epel-release
RUN yum install -y gcc python3 curl python3-pip patchelf python3-devel upx

COPY requirements.txt requirements.txt
RUN python3 -m pip install -r requirements.txt

COPY exe-requirements.txt exe-requirements.txt
RUN python3 -m pip install -r exe-requirements.txt

COPY scripts/build.sh scripts/build.sh
RUN ./scripts/build.sh

# copy PyPerf and stuff
RUN mkdir -p gprofiler/resources/ruby
RUN mkdir -p gprofiler/resources/python/pyperf
RUN cp /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
RUN cp /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
RUN cp -r /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
RUN cp /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/

COPY --from=pyspy-builder /py-spy/target/x86_64-unknown-linux-musl/release/py-spy gprofiler/resources/python/py-spy
COPY --from=rbspy-builder /rbspy/target/x86_64-unknown-linux-musl/release/rbspy gprofiler/resources/ruby/rbspy
COPY --from=perf-builder /perf gprofiler/resources/perf

COPY --from=phpspy-builder /phpspy/phpspy gprofiler/resources/php/phpspy
COPY --from=phpspy-builder /binutils/binutils-2.25/bin/bin/objdump gprofiler/resources/php/objdump
COPY --from=phpspy-builder /binutils/binutils-2.25/bin/bin/strings gprofiler/resources/php/strings
COPY --from=centos:6 /usr/bin/awk gprofiler/resources/php/awk
COPY --from=centos:6 /usr/bin/xargs gprofiler/resources/php/xargs

RUN mkdir -p gprofiler/resources/java
COPY --from=async-profiler-builder /async-profiler/async-profiler-2.0-linux-x64.tar.gz /tmp
RUN tar -xzf /tmp/async-profiler-2.0-linux-x64.tar.gz -C gprofiler/resources/java --strip-components=2 async-profiler-2.0-linux-x64/build && rm /tmp/async-profiler-2.0-linux-x64.tar.gz


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
RUN source scl_source enable devtoolset-8 llvm-toolset-7 && libs=$(./scripts/list_needed_libs.sh) && staticx $libs dist/gprofiler dist/gprofiler

FROM scratch AS export-stage

COPY --from=build-stage /app/dist/gprofiler /gprofiler
