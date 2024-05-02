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

# Since k8s change their package release manager, need to config the apt source
# more info can be foind here: https://kubernetes.io/blog/2023/08/15/pkgs-k8s-io-introduction/
# update apt for new k8s io libraries
sudo mkdir -p /etc/apt/keyrings
sudo chmod 755 /etc/apt/keyrings
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg


# this file installs requirements on a blank Ubuntu server (those are typically installed
# on GH runners, this is used for our self-hosted runners)

sudo apt update
sudo apt install -y docker.io python3-pip python-is-python3 build-essential
sudo chmod o+rw /var/run/docker.sock
