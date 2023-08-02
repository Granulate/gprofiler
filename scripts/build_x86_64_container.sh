#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail
ARCH="x86_64"
./scripts/build_x86_64_executable.sh
DOCKER_BUILDKIT=1 docker build -f container.Dockerfile -t gprofiler --build-arg ARCH=$ARCH . "$@"
