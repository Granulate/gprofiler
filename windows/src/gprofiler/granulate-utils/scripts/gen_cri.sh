#!/bin/bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
CONTAINERS_CRI="$SCRIPT_DIR/../granulate_utils/generated/containers/cri/"

# released at Oct 26, 2018
wget -O gogo.proto https://raw.githubusercontent.com/gogo/protobuf/v1.3.2/gogoproto/gogo.proto
python3 -m grpc_tools.protoc -I. --python_out="$CONTAINERS_CRI" gogo.proto
# released at Aug 27, 2021
wget -O api.proto https://raw.githubusercontent.com/kubernetes/cri-api/v0.24.0-alpha.2/pkg/apis/runtime/v1alpha2/api.proto
# '.bak' needed for BSD sed on Mac
sed -i'.bak' s,github.com/gogo/protobuf/gogoproto/gogo.proto,gogo.proto, api.proto  # patch its import
python3 -m grpc_tools.protoc -I. --python_out="$CONTAINERS_CRI" --grpc_python_out="$CONTAINERS_CRI" api.proto

sed -i'.bak' 's,import gogo_pb2,import granulate_utils.generated.containers.cri.gogo_pb2,' "$CONTAINERS_CRI/api_pb2.py"
sed -i'.bak' 's,import api_pb2,import granulate_utils.generated.containers.cri.api_pb2,' "$CONTAINERS_CRI/api_pb2_grpc.py"
rm api.proto{,.bak} gogo.proto "$CONTAINERS_CRI/api_pb2_grpc.py.bak" "$CONTAINERS_CRI/api_pb2.py.bak"
