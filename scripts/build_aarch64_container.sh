#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# ubuntu is 20.04
# rust is 1.54.0
# centos is 7

docker buildx build --platform=linux/arm64 \
    --build-arg RUST_BUILDER_VERSION=@sha256:33a923c30700bb627d1389b6819cfb18af2a585b2901df045924eba1ac0a9c30 \
    --build-arg PYPERF_BUILDER_UBUNTU=@sha256:82becede498899ec668628e7cb0ad87b6e1c371cb8a1e597d83a47fac21d6af3 \
    --build-arg PERF_BUILDER_UBUNTU=@sha256:82becede498899ec668628e7cb0ad87b6e1c371cb8a1e597d83a47fac21d6af3 \
    --build-arg PHPSPY_BUILDER_UBUNTU=@sha256:82becede498899ec668628e7cb0ad87b6e1c371cb8a1e597d83a47fac21d6af3 \
    --build-arg AP_BUILDER_CENTOS=@sha256:0f4ec88e21daf75124b8a9e5ca03c37a5e937e0e108a255d890492430789b60e \
    --build-arg GPROFILER_BUILDER_UBUNTU=@sha256:82becede498899ec668628e7cb0ad87b6e1c371cb8a1e597d83a47fac21d6af3 \
    .
