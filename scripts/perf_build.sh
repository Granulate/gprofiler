#!/usr/bin/env bash
#
# Copyright (C) 2023 Intel Corporation
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
set -euo pipefail

# downloading the zip because the git is very large (this is also very large, but still smaller)
curl -SL https://codeload.github.com/Granulate/linux/zip/5c103bf97fb268e4ea157f5e1c2a5bd6ad8c40dc -o linux.zip
unzip -qq linux.zip
rm linux.zip
cd linux-*/

# building those plugins on Aarch64's requires some of libc.a's (and other libs) .o files to
# be built "PIC", and in the default installation, they are not.
# we don't use those plugins (and we don't even deploy them) so this patch disables their build.
# (I couldn't find any way to do it with a build config to "make -C tools/perf" sadly)
if [ "$(uname -m)" = "aarch64" ]; then
    patch -p1 <<'EOF'
diff --git a/tools/lib/traceevent/plugins/Makefile b/tools/lib/traceevent/plugins/Makefile
--- a/tools/lib/traceevent/plugins/Makefile
+++ b/tools/lib/traceevent/plugins/Makefile
@@ -127,19 +127,6 @@ build := -f $(srctree)/tools/build/Makefile.build dir=. obj

 DYNAMIC_LIST_FILE := $(OUTPUT)libtraceevent-dynamic-list

-PLUGINS  = plugin_jbd2.so
-PLUGINS += plugin_hrtimer.so
-PLUGINS += plugin_kmem.so
-PLUGINS += plugin_kvm.so
-PLUGINS += plugin_mac80211.so
-PLUGINS += plugin_sched_switch.so
-PLUGINS += plugin_function.so
-PLUGINS += plugin_futex.so
-PLUGINS += plugin_xen.so
-PLUGINS += plugin_scsi.so
-PLUGINS += plugin_cfg80211.so
-PLUGINS += plugin_tlb.so
-
 PLUGINS    := $(addprefix $(OUTPUT),$(PLUGINS))
 PLUGINS_IN := $(PLUGINS:.so=-in.o)

EOF

fi

NO_LIBTRACEEVENT=1 NO_JEVENTS=1 make -C tools/perf LDFLAGS=-static -j "$(nproc)" perf
cp tools/perf/perf /
# need it static as well, even though it's used only during build (relies on libpcap, ...)
make -C tools/bpf LDFLAGS=-static -j "$(nproc)" bpftool
cp tools/bpf/bpftool/bpftool /
