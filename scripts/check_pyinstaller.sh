#!/usr/bin/env bash
#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
set -uo pipefail

# grep returns 0 if a match is found and 1 if no match is found
result=$(grep "gprofiler\." "build/pyinstaller/warn-pyinstaller.txt" | grep -v 'missing module named wmi' | grep -v 'missing module named pythoncom' | grep -v 'missing module named netifaces')

if [ -n "$result" ]; then
	echo "$result"
	echo 'PyInstaller failed to pack gProfiler code! See lines above. Make sure to check for SyntaxError as this is often the reason.';
	exit 1;
fi
