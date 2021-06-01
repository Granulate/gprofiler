#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

if [ -z ${NO_APT_INSTALL+x} ]; then
 sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update
 sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends openjdk-8-jdk python3 python3-pip docker.io php
fi

python3 -m pip install -q --upgrade setuptools pip
python3 -m pip install -q -r ./requirements.txt
python3 -m pip install -q -r ./exe-requirements.txt
python3 -m pip install -q -r ./test-requirements.txt
# TODO: python3 -m pip install .
sudo env "PATH=$PATH" python3 -m pytest -v tests/ "$@"
