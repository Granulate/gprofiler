#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# this file installs requirements on a blank Ubuntu server (those are typically installed
# on GH runners, this is used for our self-hosted runners)

sudo apt update
sudo apt install -y docker.io python3-pip python-is-python3 build-essential
sudo chmod o+rw /var/run/docker.sock
