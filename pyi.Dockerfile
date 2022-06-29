# parts are copied from Dockerfile

# these need to be defined before any FROM - otherwise, the ARGs expand to empty strings.
# see build_x86_64_executable.sh and build_aarch64_executable.sh which define these.
ARG RUST_BUILDER_VERSION
ARG PERF_BUILDER_UBUNTU
ARG PHPSPY_BUILDER_UBUNTU
ARG AP_BUILDER_CENTOS
ARG AP_BUILDER_ALPINE
ARG BURN_BUILDER_GOLANG
ARG GPROFILER_BUILDER
ARG PYPERF_BUILDER_UBUNTU

# pyspy & rbspy builder base
FROM rust${RUST_BUILDER_VERSION} AS pyspy-rbspy-builder-common
WORKDIR /tmp

COPY scripts/prepare_machine-unknown-linux-musl.sh .
RUN ./prepare_machine-unknown-linux-musl.sh

# py-spy
FROM pyspy-rbspy-builder-common AS pyspy-builder
WORKDIR /tmp
COPY scripts/pyspy_build.sh .
RUN ./pyspy_build.sh
RUN mv "/tmp/py-spy/target/$(uname -m)-unknown-linux-musl/release/py-spy" /tmp/py-spy/py-spy

# rbspy
FROM pyspy-rbspy-builder-common AS rbspy-builder
WORKDIR /tmp
COPY scripts/rbspy_build.sh .
RUN ./rbspy_build.sh
RUN mv "/tmp/rbspy/target/$(uname -m)-unknown-linux-musl/release/rbspy" /tmp/rbspy/rbspy

# perf
FROM ubuntu${PERF_BUILDER_UBUNTU} AS perf-builder
WORKDIR /tmp

COPY scripts/perf_env.sh .
RUN ./perf_env.sh

COPY scripts/libunwind_build.sh .
RUN ./libunwind_build.sh

COPY scripts/perf_build.sh .
RUN ./perf_build.sh

# phpspy
FROM ubuntu${PHPSPY_BUILDER_UBUNTU} as phpspy-builder
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

FROM golang${BURN_BUILDER_GOLANG} AS burn-builder
WORKDIR /tmp

COPY scripts/burn_build.sh .
RUN ./burn_build.sh

# bcc helpers
# built on newer Ubuntu because they require new clang (newer than available in GPROFILER_BUILDER's CentOS 7)
# these are only relevant for modern kernels, so there's no real reason to build them on CentOS 7 anyway.
FROM ubuntu${PYPERF_BUILDER_UBUNTU} AS bcc-helpers
WORKDIR /tmp

RUN if [ "$(uname -m)" = "aarch64" ]; then \
        exit 0; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        clang-10 \
        libelf-dev \
        make \
        build-essential \
        llvm \
        ca-certificates \
        git

COPY --from=perf-builder /bpftool /bpftool

COPY scripts/bcc_helpers_build.sh .
RUN ./bcc_helpers_build.sh


# bcc & gprofiler
FROM centos${GPROFILER_BUILDER} AS build-stage
WORKDIR /bcc

# fix repo links for CentOS 8, and enable powertools (required to download glibc-static)
RUN if grep -q "CentOS Linux 8" /etc/os-release ; then \
        sed -i 's/mirrorlist/#mirrorlist/g' /etc/yum.repos.d/CentOS-*; \
        sed -i 's|#baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' /etc/yum.repos.d/CentOS-*; \
        yum install -y dnf-plugins-core; \
        dnf config-manager --set-enabled powertools; \
        yum clean all; \
    fi

# bcc part
# TODO: copied from the main Dockerfile... but modified a lot. we'd want to share it some day.

RUN yum install -y git && yum clean all

# these are needed to build PyPerf, which we don't build on Aarch64, hence not installing them here.
RUN if [ "$(uname -m)" = "aarch64" ]; then exit 0; fi; yum install -y \
    curl \
    cmake \
    patch \
    python3 \
    flex \
    bison \
    zlib-devel.x86_64 \
    xz-devel \
    ncurses-devel \
    elfutils-libelf-devel && \
    yum clean all

RUN if [ "$(uname -m)" = "aarch64" ]; \
        then exit 0; \
    fi && \
    yum install -y centos-release-scl-rh && \
    yum clean all
# mostly taken from https://github.com/iovisor/bcc/blob/master/INSTALL.md#install-and-compile-llvm
RUN if [ "$(uname -m)" = "aarch64" ]; \
        then exit 0; \
    fi && \
    yum install -y devtoolset-8 \
        llvm-toolset-7 \
        llvm-toolset-7-llvm-devel \
        llvm-toolset-7-llvm-static \
        llvm-toolset-7-clang-devel \
        devtoolset-8-elfutils-libelf-devel && \
    yum clean all

COPY ./scripts/libunwind_build.sh .
RUN if [ "$(uname -m)" = "aarch64" ]; then \
        exit 0; \
    fi && \
    ./libunwind_build.sh

COPY ./scripts/pyperf_build.sh .
# hadolint ignore=SC1091
RUN set -e; \
    if [ "$(uname -m)" != "aarch64" ]; then \
        source scl_source enable devtoolset-8 llvm-toolset-7; \
    fi && \
    source ./pyperf_build.sh

# gProfiler part

WORKDIR /app

RUN yum clean all && yum --setopt=skip_missing_names_on_install=False install -y \
        epel-release \
        gcc \
        python3 \
        curl \
        python3-pip \
        python3-devel

# needed for aarch64 (for staticx)
RUN set -e; \
    if [ "$(uname -m)" = "aarch64" ]; then \
        yum install -y glibc-static zlib-devel.aarch64 && \
        yum clean all; \
    fi
# needed for aarch64, scons & wheel are needed to build staticx
RUN set -e; \
    if [ "$(uname -m)" = "aarch64" ]; then \
        python3 -m pip install --no-cache-dir 'wheel==0.37.0' 'scons==4.2.0'; \
    fi

# we want the latest pip
# hadolint ignore=DL3013
RUN python3 -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt requirements.txt
COPY granulate-utils/setup.py granulate-utils/requirements.txt granulate-utils/README.md granulate-utils/
COPY granulate-utils/granulate_utils granulate-utils/granulate_utils
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY exe-requirements.txt exe-requirements.txt
# build on centos:8 of Aarch64 requires -lnss_files and -lnss_dns. the files are missing but the symbols
# seem to be provided from another archive (e.g libc.a), so this "fix" bypasses the ld error of "missing -lnss..."
# see https://github.com/JonathonReinhart/staticx/issues/219
RUN if grep -q "CentOS Linux 8" /etc/os-release ; then \
    ! test -f /lib64/libnss_files.a && ar rcs /lib64/libnss_files.a && \
    ! test -f /lib64/libnss_dns.a && ar rcs /lib64/libnss_dns.a; \
    fi
RUN python3 -m pip install --no-cache-dir -r exe-requirements.txt

# copy PyPerf, licenses and notice file.
RUN mkdir -p gprofiler/resources/ruby && \
    mkdir -p gprofiler/resources/python/pyperf && \
    cp /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/ && \
    cp /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/ && \
    cp -r /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses && \
    cp /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/
COPY --from=bcc-helpers /bpf_get_fs_offset/get_fs_offset gprofiler/resources/python/pyperf/
COPY --from=bcc-helpers /bpf_get_stack_offset/get_stack_offset gprofiler/resources/python/pyperf/

COPY --from=pyspy-builder /tmp/py-spy/py-spy gprofiler/resources/python/py-spy
COPY --from=rbspy-builder /tmp/rbspy/rbspy gprofiler/resources/ruby/rbspy
COPY --from=perf-builder /perf gprofiler/resources/perf

COPY --from=phpspy-builder /tmp/phpspy/phpspy gprofiler/resources/php/phpspy
COPY --from=phpspy-builder /tmp/binutils/binutils-2.25/bin/bin/objdump gprofiler/resources/php/objdump
COPY --from=phpspy-builder /tmp/binutils/binutils-2.25/bin/bin/strings gprofiler/resources/php/strings
# copying from async-profiler-builder as an "old enough" centos.
COPY --from=async-profiler-builder-glibc /usr/bin/awk gprofiler/resources/php/awk
COPY --from=async-profiler-builder-glibc /usr/bin/xargs gprofiler/resources/php/xargs

COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/jattach gprofiler/resources/java/jattach
COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/async-profiler-version gprofiler/resources/java/async-profiler-version
COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/libasyncProfiler.so gprofiler/resources/java/glibc/libasyncProfiler.so
COPY --from=async-profiler-builder-musl /tmp/async-profiler/build/libasyncProfiler.so gprofiler/resources/java/musl/libasyncProfiler.so
COPY --from=async-profiler-builder-glibc /tmp/async-profiler/build/fdtransfer gprofiler/resources/java/fdtransfer

COPY --from=burn-builder /tmp/burn/burn gprofiler/resources/burn

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

# for aarch64 - build a patched version of staticx 0.13.6. we remove calls to getpwnam and getgrnam, for these end up doing dlopen()s which
# crash the staticx bootloader. we don't need them anyway (all files in our staticx tar are uid 0 and we don't need the names translation)
COPY scripts/staticx_patch.diff staticx_patch.diff
# hadolint ignore=DL3003
RUN if [ "$(uname -m)" = "aarch64" ]; then \
        git clone -b v0.13.6 https://github.com/JonathonReinhart/staticx.git && \
        cd staticx && \
        git reset --hard 819d8eafecbaab3646f70dfb1e3e19f6bbc017f8 && \
        git apply ../staticx_patch.diff && \
        python3 -m pip install --no-cache-dir . ; \
    fi

RUN yum install -y patchelf upx && yum clean all

COPY ./scripts/list_needed_libs.sh ./scripts/list_needed_libs.sh
# staticx packs dynamically linked app with all of their dependencies, it tries to figure out which dynamic libraries are need for its execution
# in some cases, when the application is lazily loading some DSOs, staticx doesn't handle it.
# we use list_needed_libs.sh to list the dynamic dependencies of *all* of our resources,
# and make staticx pack them as well.
# using scl here to get the proper LD_LIBRARY_PATH set
# hadolint ignore=SC2046
RUN set -e; \
    if [ $(uname -m) != "aarch64" ]; then \
        source scl_source enable devtoolset-8 llvm-toolset-7 ; \
    fi && \
    staticx $(./scripts/list_needed_libs.sh) dist/gprofiler dist/gprofiler

FROM scratch AS export-stage

COPY --from=build-stage /app/dist/gprofiler /gprofiler
