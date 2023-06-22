#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"  # https://stackoverflow.com/a/246128

if [ -z ${NO_APT_INSTALL+x} ]; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends openjdk-8-jdk python3 python3-pip docker.io php
  if [ "$(uname -m)" = "aarch64" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends python3-dev ruby3.0 build-essential nodejs
    if ! [ -L "/usr/bin/dotnet" ] ; then
      # there is no dotnet apt package on aarch64
      curl -SL -o dotnet.tar.gz https://dotnetcli.blob.core.windows.net/dotnet/Sdk/master/dotnet-sdk-latest-linux-arm64.tar.gz
      sudo mkdir -p /usr/share/dotnet
      sudo tar -zxf dotnet.tar.gz -C /usr/share/dotnet
      sudo ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet
    fi
  else
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends ruby2.7 dotnet-sdk-6.0
  fi
fi

# we'll check 1 file, perf. not if --executable is passed - these are the executable tests, and they don't
# require resources.
# TODO split them to 2 pytest files
PERF_RESOURCE="$SCRIPT_DIR/../gprofiler/resources/perf"
if [ ! -f "$PERF_RESOURCE" ] && [[ "$*" != *"--executable"* ]]; then
    echo "perf resource not found: $(readlink -f "$PERF_RESOURCE")"
    echo "Please run $(readlink -f "$SCRIPT_DIR/../scripts/copy_resources_from_image.sh") to get all resources"
    exit 1
fi

python3 -m pip install -q --upgrade setuptools pip
python3 -m pip install -r ./requirements.txt -r ./exe-requirements.txt -r ./dev-requirements.txt
# TODO: python3 -m pip install .
sudo env "PATH=$PATH" python3 -m pytest -v tests/ "$@"
