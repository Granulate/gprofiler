#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# fix legacy yum repos. slightly adapted from https://stackoverflow.com/a/53848450
# this basically comments out all mirrorlist URLs, uncomments all baseurls (all seem to be commented)
# and replaces the domain from mirror.centos.org to vault.centos.org.
sed -i -e 's|^mirrorlist|#mirrorlist|' -e 's|^# *baseurl|baseurl|' -e 's|mirror.centos.org|vault.centos.org|' /etc/yum.repos.d/*

yum install -y gcc g++ gcc-c++.x86_64 make java-1.8.0-openjdk-devel glibc-static git
