#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# downloading the zip because the git is very large (this is also very large, but still smaller)
curl -SL https://codeload.github.com/Granulate/linux/zip/aa689e9b55c7b5ba8d399bc2560d36ef98436150 -o linux.zip
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

make -C tools/perf LDFLAGS=-static -j 8 perf
cp tools/perf/perf /
# need it static as well, even though it's used only during build (relies on libpcap, ...)
make -C tools/bpf LDFLAGS=-static -j 8 bpftool
cp tools/bpf/bpftool/bpftool /
