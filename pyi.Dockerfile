# copied from Dockerfile
FROM rust:latest AS pyspy-builder

COPY scripts/pyspy_env.sh .
RUN ./pyspy_env.sh

COPY scripts/pyspy_build.sh .
RUN ./pyspy_build.sh

# Centos 7 image is used to grab an old version of `glibc` during `pyinstaller` bundling.
# This will allow the executable to run on older versions of the kernel, eventually leading to the executable running on a wider range of machines.
FROM centos:7 AS build-stage

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

RUN git clone --depth 1 -b v1.0.1 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 92b61ade89f554859950695b067288f60cb1f3e5

RUN yum install -y centos-release-scl-rh
# mostly taken from https://github.com/iovisor/bcc/blob/master/INSTALL.md#install-and-compile-llvm
RUN yum install -y devtoolset-8 \
    llvm-toolset-7 \
    llvm-toolset-7-llvm-devel \
    llvm-toolset-7-llvm-static \
    llvm-toolset-7-clang-devel \
    devtoolset-8-elfutils-libelf-devel

RUN mkdir bcc/build && cd bcc/build && \
  source scl_source enable devtoolset-8 llvm-toolset-7 && \
  cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root .. && \
  make -C examples/cpp/pyperf -j -l VERBOSE=1 install

# gProfiler part

WORKDIR /app

RUN yum update -y && yum install -y epel-release
RUN yum install -y gcc python3 curl python3-pip patchelf python3-devel upx

COPY requirements.txt requirements.txt
RUN python3 -m pip install -r requirements.txt

COPY dev-requirements.txt dev-requirements.txt
RUN python3 -m pip install -r dev-requirements.txt

COPY scripts/build.sh scripts/build.sh
RUN ./scripts/build.sh

# copy PyPerf and stuff
RUN cp /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
RUN cp /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
RUN cp -r /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
RUN cp /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/

COPY --from=pyspy-builder /py-spy/target/x86_64-unknown-linux-musl/release/py-spy gprofiler/resources/python/py-spy

COPY gprofiler gprofiler

# run PyInstaller and make sure no 'gprofiler.*' modules are missing.
# see https://pyinstaller.readthedocs.io/en/stable/when-things-go-wrong.html
# from a quick look I didn't see how to tell PyInstaller to exit with an error on this, hence
# this check in the shell.
COPY pyi_build.py pyinstaller.spec .
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
