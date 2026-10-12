#!/bin/bash

# Re-run as root:
test $EUID = 0 || exec sudo "$0" "$@"

# EMR bootstrap actions receive credentials only as arguments. Move --token / --service-name into
# gProfiler's environment (configargparse reads GPROFILER_TOKEN / GPROFILER_SERVICE_NAME) so they
# don't appear in the long-running gProfiler's /proc/<pid>/cmdline, which is world-readable.
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --token=*) export GPROFILER_TOKEN="${1#--token=}" ;;
        --token) export GPROFILER_TOKEN="${2-}"; shift ;;
        --service-name=*) export GPROFILER_SERVICE_NAME="${1#--service-name=}" ;;
        --service-name) export GPROFILER_SERVICE_NAME="${2-}"; shift ;;
        *) args+=("$1") ;;
    esac
    shift
done

version=latest
arch=$(uname -m)

wget "https://github.com/intel/gprofiler/releases/$version/download/gprofiler_$arch" -O gprofiler
chmod +x gprofiler
setsid ./gprofiler -cu "${args[@]}" >/dev/null 2>&1 &
