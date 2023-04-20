#!/usr/bin/env bash

set -euo pipefail

MODULE_PATH=/tmp/module
BUILD_TARGET_DIR=/tmp/module_build

GIT_REV=20eb88a
git clone https://github.com/mmarchini-oss/node-linux-perf.git $MODULE_PATH
cd $MODULE_PATH
git reset --hard $GIT_REV

npm install -g node-gyp
curl -L https://github.com/nodejs/nan/archive/refs/tags/v2.16.0.tar.gz -o nan.tar.gz
tar -vxzf nan.tar.gz -C /tmp
NAN_PATH=$(realpath /tmp/nan-*)
export NAN_PATH
# shellcheck disable=SC2016 # expression shouldn't be expanded
# providing nan module by path, rather than npm, because of using node-gyp instead of npm
sed -i 's/node \-e \\"require('\''nan'\'')\\"/echo $NAN_PATH/g' binding.gyp
rm -rf nan.tar.gz
mkdir $BUILD_TARGET_DIR
# Need to build with static libstdc++ to avoid https://github.com/Granulate/gprofiler/issues/604 centos7 issue. 
if grep -q "CentOS Linux 8" /etc/os-release; then
    export LDFLAGS="-static-libstdc++"
fi
node_versions=( "10.10.0" "11.0.0" )
for node_version in "${node_versions[@]}"; do
    node-gyp configure --target="$node_version"  --build_v8_with_gn=false
    node-gyp build --target="$node_version"
    # shellcheck disable=SC2206 # string is expected to be splitted here
    t=(${node_version//./ })
    node_major_version=${t[0]}
    mkdir -p "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version"
    cp "$MODULE_PATH/linux-perf.js" "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version/."
    mkdir -p "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version/build/Release"
    # we need to preserve original path required by linux-perf.js
    cp "$MODULE_PATH/build/Release/linux-perf.node" "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version/build/Release/linux-perf.node"
    rm -rf "$MODULE_PATH/build"
done
for node_major_version in {12..16}; do
    node-gyp configure --target="$node_major_version.0.0"
    node-gyp build --target="$node_major_version.0.0"
    mkdir -p "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version"
    cp "$MODULE_PATH/linux-perf.js" "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version/."
    mkdir -p "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version/build/Release"
    cp "$MODULE_PATH/build/Release/linux-perf.node" "$BUILD_TARGET_DIR/$GIT_REV/$node_major_version/build/Release/linux-perf.node"
    rm -rf "$MODULE_PATH/build"
done
rm -rf "$NAN_PATH"
echo -n "$GIT_REV" > $BUILD_TARGET_DIR/version