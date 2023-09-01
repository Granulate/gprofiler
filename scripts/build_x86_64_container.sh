#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail
ARCH="x86_64"
if [ "$#" -gt 0 ] && [ "$1" == "--skip-exe-build" ]; then
    shift
else
    ./scripts/build_x86_64_executable.sh
fi
docker buildx build -f container.Dockerfile -t gprofiler --build-arg ARCH=$ARCH . "$@"
