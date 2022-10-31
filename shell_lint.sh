#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

SHELLCHECK_VERSION=v0.8.0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
# shellcheck disable=SC2046
docker run --rm -t -v "$SCRIPT_DIR":"$SCRIPT_DIR" "koalaman/shellcheck:$SHELLCHECK_VERSION" -x $(git ls-tree -r HEAD --name-only | grep '.sh$' | sed "s,.*,$SCRIPT_DIR/&,")
