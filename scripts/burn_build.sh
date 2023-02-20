#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail
DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

git clone -b "$(awk '/VERSION/{print $2}' <"${DIR}/burn_version.txt")" https://github.com/granulate/burn
cd burn
git reset --hard "$(awk '/COMMIT/{print $2}' <"${DIR}/burn_version.txt")"

CGO_ENABLED=0 go build
