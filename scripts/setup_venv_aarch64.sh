#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

virtualenv --python python3.8 aarch64-venv
echo 'source ${PWD}/aarch64-venv/bin/activate' >> ~/.bashrc