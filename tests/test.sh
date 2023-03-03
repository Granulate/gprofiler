#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"  # https://stackoverflow.com/a/246128

if [ -z ${NO_APT_INSTALL+x} ]; then
   if [ "$(uname -m)" = "aarch64" ]; then
    curl -SL -o dotnet.tar.gz https://dotnetcli.blob.core.windows.net/dotnet/Sdk/master/dotnet-sdk-latest-linux-arm64.tar.gz
    sudo mkdir -p /usr/share/dotnet
    sudo tar -zxf dotnet.tar.gz -C /usr/share/dotnet
    sudo ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet
    sudo DEBIAN_FRONTEND=noninteractive apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-8-jdk python3 python3-pip sudo apt-get install python3-dev docker.io php ruby3.0
    mkdir -vp ~/.docker/cli-plugins/
    curl --silent -L "https://github.com/docker/buildx/releases/download/v0.10.2/buildx-v0.10.2.linux-arm64" > ~/.docker/cli-plugins/docker-buildx
    chmod a+x ~/.docker/cli-plugins/docker-buildx
  else
    sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends gcc openjdk-8-jdk python3 python3-pip docker.io php ruby2.7 dotnet-sdk-6.0
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
