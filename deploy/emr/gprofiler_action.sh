#!/bin/bash

# Re-run as root:
test $EUID = 0 || exec sudo "$0" "$@"

version=latest
arch=$(uname -m)

wget "https://github.com/Granulate/gprofiler/releases/$version/download/gprofiler_$arch" -O gprofiler
chmod +x gprofiler
# Must supply --token=... and --service-name=... arguments when creating cluster
setsid ./gprofiler -cu "$@" >/dev/null 2>&1 &
