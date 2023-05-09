#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail
if [ "$(uname -m)" = "aarch64" ]
then
    ARCH="aarch64"
else
    ARCH="x86_64"
fi
DOCKER_BUILDKIT=1 docker build . -t gprofiler-test --build-arg ARCH=$ARCH "$@"
