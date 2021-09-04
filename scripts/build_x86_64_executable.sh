#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

mkdir -p output
DOCKER_BUILDKIT=1 docker build -f pyi.Dockerfile --output type=local,dest=output/ .
