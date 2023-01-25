#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -uo pipefail

# grep returns 0 if a match is found and 1 if no match is found
result=$(grep "gprofiler\." "build/pyinstaller/warn-pyinstaller.txt" | grep -v 'missing module named wmi' | grep -v 'missing module named pythoncom' | grep -v 'missing module named netifaces')

if [ -n "$result" ]; then
	echo "$result"
	echo 'PyInstaller failed to pack gProfiler code! See lines above. Make sure to check for SyntaxError as this is often the reason.';
	exit 1;
fi
