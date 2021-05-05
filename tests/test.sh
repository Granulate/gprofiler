#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e
umask
exit 1

sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends openjdk-8-jdk python3 python3-pip docker.io
python3 -m pip install -q --upgrade setuptools pip
python3 -m pip install -q -r ./requirements.txt
python3 -m pip install -q pytest docker
# TODO: python3 -m pip install .
sudo python3 -m pytest -v tests/
