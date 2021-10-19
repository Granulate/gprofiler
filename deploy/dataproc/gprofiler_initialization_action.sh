#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
#!/bin/bash
set -euxo pipefail

GPROFILER_TOKEN=$(/usr/share/google/get_metadata_value attributes/gprofiler-token)
readonly GPROFILER_TOKEN

GPROFILER_SERVICE=$(/usr/share/google/get_metadata_value attributes/gprofiler-service)
readonly GPROFILER_SERVICE

wget https://github.com/Granulate/gprofiler/releases/latest/download/gprofiler
sudo chmod +x gprofiler
sudo sh -c "setsid ./gprofiler -cu --token '$GPROFILER_TOKEN' --service-name '$GPROFILER_SERVICE' > /dev/null 2>&1 &"
echo "gProfiler installed successfully."
