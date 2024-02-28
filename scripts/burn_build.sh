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
DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

git clone -b "$(awk '/VERSION/{print $2}' <"${DIR}/burn_version.txt")" https://github.com/granulate/burn
cd burn
git reset --hard "$(awk '/COMMIT/{print $2}' <"${DIR}/burn_version.txt")"

CGO_ENABLED=0 go build
