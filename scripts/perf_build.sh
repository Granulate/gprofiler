#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# downloading the zip because the git is very large (this is also very large, but still smaller)
curl -SL https://codeload.github.com/Granulate/linux/zip/40a7823cf90a7e69ce8af88d224dfdd7e371de2d -o linux.zip
unzip -qq linux.zip
rm linux.zip
cd linux-*/

# Aarch64's libc has no "PIC" version. libtraceevent has some dynamic plugins it's trying to build.
# we don't use them (and we don't even deploy them) so this patch disables their build.
# (I couldn't find any way to do it with a build config to "make -C tools/perf" sadly)
if [ $(uname -m) = "aarch64" ]; then
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
cp tools/perf/perf /perf
