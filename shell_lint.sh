#!/usr/bin/env bash
#
# Copyright (C) 2022 Intel Corporation
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

SHELLCHECK_VERSION=v0.8.0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
# shellcheck disable=SC2046
docker run --rm -t -v "$SCRIPT_DIR":"$SCRIPT_DIR" "koalaman/shellcheck:$SHELLCHECK_VERSION" -x $(git ls-tree -r HEAD --name-only | grep '.sh$' | sed "s,.*,$SCRIPT_DIR/&,")
