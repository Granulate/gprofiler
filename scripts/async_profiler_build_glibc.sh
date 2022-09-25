#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# This script is invoked by async_profiler_build_shared.sh
set +eu  # this funny script has errors :shrug:
# shellcheck disable=SC1091  # not checkable in shellcheck context
source scl_source enable devtoolset-7
set -eu
