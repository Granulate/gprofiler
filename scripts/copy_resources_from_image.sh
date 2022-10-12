#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

IMAGE=${1:-granulate/gprofiler:latest}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"

# copy the "gprofiler/resources" directory from the gprofiler container

CONTAINER=$(docker container create "$IMAGE")
set +e
docker cp "$CONTAINER:/app/gprofiler/resources" "$SCRIPT_DIR/../gprofiler/"
RET=$?
set -e
docker rm "$CONTAINER"
exit "$RET"
