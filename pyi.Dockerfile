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

COPY . .
RUN pyinstaller pyinstaller.spec
# staticx packs dynamically linked app with all of their dependencies, it tries to figure out which dynamic libraries are need for its execution
# in some cases, when the application is lazily loading some DSOs, staticx doesn't handle it.
# libcgcc_s is such a library, so we pass it manually to staticx.
RUN staticx -l /usr/lib/gcc/x86_64-redhat-linux/4.8.2/libgcc_s.so dist/gprofiler dist/gprofiler

FROM scratch AS export-stage

COPY --from=build-stage /app/dist/gprofiler /gprofiler
