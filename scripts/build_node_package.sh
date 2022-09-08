#!/usr/bin/env bash

set -euo pipefail

MODULE_PATH=/tmp/module
BUILD_TARGET_DIR=/tmp/module_build

git clone https://github.com/mmarchini-oss/node-linux-perf.git $MODULE_PATH
cd $MODULE_PATH
git reset --hard 20eb88a35ab256313dfb0f14645456ebf046ac1b

npm install -g node-gyp
curl -L https://github.com/nodejs/nan/archive/refs/tags/v2.16.0.tar.gz -o nan.tar.gz
tar -vxzf nan.tar.gz -C /tmp
NAN_PATH=$(realpath /tmp/nan-*)
export NAN_PATH
sed -i 's/node \-e \\"require('\''nan'\'')\\"/echo $NAN_PATH/g' binding.gyp
rm -rf nan.tar.gz
mkdir $BUILD_TARGET_DIR
node_versions=( "10.10.0" "11.0.0" )
for node_version in "${node_versions[@]}"; do
    node-gyp configure --target="$node_version"  --build_v8_with_gn=false
    node-gyp build --target="$node_version"
    t=(${node_version//./ })
    node_major_version=${t[0]}
    mkdir "$BUILD_TARGET_DIR/$node_major_version"
    cp "$MODULE_PATH/linux-perf.js" "$BUILD_TARGET_DIR/$node_major_version/."
    mkdir -p "$BUILD_TARGET_DIR/$node_major_version/build/Release"
    cp "$MODULE_PATH/build/Release/linux-perf.node" "$BUILD_TARGET_DIR/$node_major_version/build/Release/linux-perf.node"
    rm -rf "$MODULE_PATH/build"
done
for node_major_version in {12..16}; do
    node-gyp configure --target="$node_major_version.0.0"
    node-gyp build --target="$node_major_version.0.0"
    mkdir "$BUILD_TARGET_DIR/$node_major_version"
    cp "$MODULE_PATH/linux-perf.js" "$BUILD_TARGET_DIR/$node_major_version/."
    mkdir -p "$BUILD_TARGET_DIR/$node_major_version/build/Release"
    cp "$MODULE_PATH/build/Release/linux-perf.node" "$BUILD_TARGET_DIR/$node_major_version/build/Release/linux-perf.node"
    rm -rf "$MODULE_PATH/build"
done
rm -rf "$NAN_PATH"