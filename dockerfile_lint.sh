#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

HADOLINT_VERSION=v2.9.2

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
docker run --rm -v "$SCRIPT_DIR:$SCRIPT_DIR" "hadolint/hadolint:$HADOLINT_VERSION" hadolint "$SCRIPT_DIR/Dockerfile" -c "$SCRIPT_DIR/.hadolint.yaml" "$SCRIPT_DIR/pyi.Dockerfile"
