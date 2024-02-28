#!/usr/bin/env bash
#
# Copyright (C) 2023 Intel Corporation
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
