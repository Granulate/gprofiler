#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# pyspy & rbspy, using the same builder for both pyspy and rbspy since they share build dependencies - rust:1.59-alpine3.15
RUST_BUILDER_VERSION=@sha256:65b63b7d003f7a492cc8e550a4830aaa1f4155b74387549a82985c8efb3d0e88
# perf - ubuntu:16.04 (IIRC for older glibc, to support older kernels)
UBUNTU_VERSION_1604=@sha256:d7bb0589725587f2f67d0340edb81fd1fcba6c5f38166639cf2a252c939aa30c
# phpspy & pyperf - ubuntu:20.04
UBUNTU_VERSION=@sha256:cf31af331f38d1d7158470e095b132acd126a7180a54f263d386da88eb681d93
# async-profiler glibc - centos:7
AP_BUILDER_CENTOS=@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e
# async-profiler musl - alpine
AP_BUILDER_ALPINE=@sha256:69704ef328d05a9f806b6b8502915e6a0a4faa4d72018dc42343f511490daf8a
# dotnet builder - mcr.microsoft.com/dotnet/sdk:6.0-focal
DOTNET_BUILDER=@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22
# minimum CentOS version we intend to support with async-profiler (different between x86_64, where we require
# an older version)
AP_CENTOS_MIN=:6
# burn - golang:1.16.3
BURN_BUILDER_GOLANG=@sha256:f7d3519759ba6988a2b73b5874b17c5958ac7d0aa48a8b1d84d66ef25fa345f1
# bcc & gprofiler - centos:7
# CentOS 7 image is used to grab an old version of `glibc` during `pyinstaller` bundling.
# this will allow the executable to run on older versions of the kernel, eventually leading to the executable running on a wider range of machines.
GPROFILER_BUILDER=@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e
# node-package-builder-glibc - centos/devtoolset-7-toolchain-centos7:latest
NODE_PACKAGE_BUILDER_GLIBC=centos/devtoolset-7-toolchain-centos7@sha256:24d4c230cb1fe8e68cefe068458f52f69a1915dd6f6c3ad18aa37c2b8fa3e4e1

mkdir -p build/x86_64
DOCKER_BUILDKIT=1 docker build -f pyi.Dockerfile --output type=local,dest=build/x86_64/ \
    --build-arg RUST_BUILDER_VERSION=$RUST_BUILDER_VERSION \
    --build-arg PYPERF_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg PERF_BUILDER_UBUNTU=$UBUNTU_VERSION_1604 \
    --build-arg PHPSPY_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg AP_BUILDER_CENTOS=$AP_BUILDER_CENTOS \
    --build-arg AP_BUILDER_ALPINE=$AP_BUILDER_ALPINE \
    --build-arg AP_CENTOS_MIN=$AP_CENTOS_MIN \
    --build-arg BURN_BUILDER_GOLANG=$BURN_BUILDER_GOLANG \
    --build-arg GPROFILER_BUILDER=$GPROFILER_BUILDER \
    --build-arg DOTNET_BUILDER=$DOTNET_BUILDER \
    --build-arg NODE_PACKAGE_BUILDER_MUSL=$AP_BUILDER_ALPINE \
    --build-arg NODE_PACKAGE_BUILDER_GLIBC=$NODE_PACKAGE_BUILDER_GLIBC \
    . "$@"
