# TODO: copied from the main Dockerfile... too bad we don't have includes.
FROM ubuntu:20.04 as bcc-builder

RUN apt-get update

RUN DEBIAN_FRONTEND=noninteractive apt-get install -y git build-essential iperf llvm-9-dev libclang-9-dev \
  cmake python3 flex bison libelf-dev libz-dev

WORKDIR /bcc

RUN git clone --depth 1 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 119d71bf9681182759eb76d40660c0ec19f3fc42
RUN mkdir bcc/build && cd bcc/build && \
  cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root .. && \
  make -C examples/cpp/pyperf -j -l VERBOSE=1 install


# Centos 7 image is used to grab an old version of `glibc` during `pyinstaller` bundling.
# This will allow the executable to run on older versions of the kernel, eventually leading to the executable running on a wider range of machines.
FROM centos:7 as build-stage
WORKDIR /app

RUN yum update -y && yum install -y epel-release
RUN yum install -y gcc python3 curl python3-pip patchelf python3-devel
COPY requirements.txt requirements.txt
COPY dev-requirements.txt dev-requirements.txt
COPY build.sh build.sh
RUN python3 -m pip install -r dev-requirements.txt
RUN python3 -m pip install -r requirements.txt
RUN ./build.sh

COPY --from=bcc-builder /bcc/root/share/bcc/examples/cpp/PyPerf gprofiler/resources/python/pyperf/
# copy licenses and notice file.
COPY --from=bcc-builder /bcc/bcc/LICENSE.txt gprofiler/resources/python/pyperf/
COPY --from=bcc-builder /bcc/bcc/licenses gprofiler/resources/python/pyperf/licenses
COPY --from=bcc-builder /bcc/bcc/NOTICE gprofiler/resources/python/pyperf/

COPY . .
RUN pyinstaller pyinstaller.spec
# staticx packs dynamically linked app with all of their dependencies, it tries to figure out which dynamic libraries are need for its execution
# in some cases, when the application is lazily loading some DSOs, staticx doesn't handle it.
# libcgcc_s is such a library, so we pass it manually to staticx.
RUN staticx -l /usr/lib/gcc/x86_64-redhat-linux/4.8.2/libgcc_s.so dist/gprofiler dist/gprofiler

FROM scratch AS export-stage

COPY --from=build-stage /app/dist/gprofiler /gprofiler
