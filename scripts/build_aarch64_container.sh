#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail
ARCH="aarch64"
if [ "$#" -gt 0 ] && [ "$1" == "--skip-exe-build" ]; then
    shift
else
    ./scripts/build_aarch64_executable.sh
fi
docker buildx build -f container.Dockerfile --build-arg ARCH=$ARCH . "$@"
