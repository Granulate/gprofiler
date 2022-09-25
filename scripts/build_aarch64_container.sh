#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# ubuntu 20.04
UBUNTU_VERSION=@sha256:82becede498899ec668628e7cb0ad87b6e1c371cb8a1e597d83a47fac21d6af3
# rust 1.54.0
RUST_VERSION=@sha256:33a923c30700bb627d1389b6819cfb18af2a585b2901df045924eba1ac0a9c30
# centos 7
CENTOS_VERSION=@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e
# golang 1.16.3
GOLANG_VERSION=@sha256:f7d3519759ba6988a2b73b5874b17c5958ac7d0aa48a8b1d84d66ef25fa345f1
# alpine 3.14.2
ALPINE_VERSION=@sha256:b06a5cf61b2956088722c4f1b9a6f71dfe95f0b1fe285d44195452b8a1627de7
# mcr.microsoft.com/dotnet/sdk:6.0-focal
DOTNET_BUILDER=@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22

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
    . "$@"
