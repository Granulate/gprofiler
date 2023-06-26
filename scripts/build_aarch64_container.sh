#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# ubuntu 20.04
UBUNTU_VERSION=@sha256:82becede498899ec668628e7cb0ad87b6e1c371cb8a1e597d83a47fac21d6af3
# rust:1.59-alpine3.15
RUST_VERSION=@sha256:65b63b7d003f7a492cc8e550a4830aaa1f4155b74387549a82985c8efb3d0e88
# centos 7
CENTOS_VERSION=@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e
# golang 1.16.3
GOLANG_VERSION=@sha256:f7d3519759ba6988a2b73b5874b17c5958ac7d0aa48a8b1d84d66ef25fa345f1
# alpine 3.14.2
ALPINE_VERSION=@sha256:b06a5cf61b2956088722c4f1b9a6f71dfe95f0b1fe285d44195452b8a1627de7
# mcr.microsoft.com/dotnet/sdk:6.0-focal
DOTNET_BUILDER=@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22
# node-package-builder-glibc - centos:8
NODE_PACKAGE_BUILDER_GLIBC=centos@sha256:65a4aad1156d8a0679537cb78519a17eb7142e05a968b26a5361153006224fdc

docker buildx build --platform=linux/arm64 \
    --build-arg RUST_BUILDER_VERSION=$RUST_VERSION \
    --build-arg PYPERF_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg PERF_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg PHPSPY_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg AP_BUILDER_CENTOS=$CENTOS_VERSION \
    --build-arg AP_BUILDER_ALPINE=$ALPINE_VERSION \
    --build-arg BURN_BUILDER_GOLANG=$GOLANG_VERSION \
    --build-arg GPROFILER_BUILDER_UBUNTU=$UBUNTU_VERSION \
    --build-arg DOTNET_BUILDER=$DOTNET_BUILDER \
    --build-arg NODE_PACKAGE_BUILDER_MUSL=$ALPINE_VERSION \
    --build-arg NODE_PACKAGE_BUILDER_GLIBC=$NODE_PACKAGE_BUILDER_GLIBC \
    . "$@"
