#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import logging
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import psutil

CGROUPFS_ROOT = "/sys/fs/cgroup"  # TODO extract from /proc/mounts, this may change


class UsageLoggerInterface:
    def init_cycles(self):
        raise NotImplementedError

    def log_cycle(self):
        raise NotImplementedError

    def log_run(self):
        raise NotImplementedError


class CpuUsageLogger(UsageLoggerInterface):
    NSEC_PER_SEC = 1000000000

    def __init__(self, logger: logging.LoggerAdapter, cgroup: str):
        self._logger = logger
        self._cpuacct_usage = Path(f"{CGROUPFS_ROOT}/{cgroup}cpuacct/cpuacct.usage")
        self._last_usage: Optional[int] = None
        self._last_ts: Optional[float] = None

    def _read_cgroup_cpu_usage(self) -> int:
        """
        Reads the current snapshot of cpuacct.usage for a cgroup.
        """
        return int(self._cpuacct_usage.read_text())

    def init_cycles(self):
        self._last_usage = self._read_cgroup_cpu_usage()
        self._last_ts = time.monotonic()

    def log_cycle(self):
        assert self._last_usage is not None and self._last_ts is not None, "didn't call init_cycles()?"

        now_usage = self._read_cgroup_cpu_usage()
        now_ts = time.monotonic()

        diff_usage = now_usage - self._last_usage
        diff_usage_s = diff_usage / self.NSEC_PER_SEC
        diff_ts = now_ts - self._last_ts

        self._logger.debug(
            f"CPU usage this cycle: {diff_usage_s:.3f}"
            f" seconds {diff_usage_s / diff_ts * 100:.2f}% ({diff_usage} cgroup time)"
        )

        self._last_usage = now_usage
        self._last_ts = now_ts

    def log_run(self):
        total_usage = self._read_cgroup_cpu_usage()
        total_usage_s = total_usage / self.NSEC_PER_SEC
        total_ts = time.time() - psutil.Process().create_time()  # uptime of this process

        self._logger.debug(
            f"Total CPU usage this run: {total_usage / self.NSEC_PER_SEC:.3f} seconds"
            f" {total_usage_s / total_ts * 100:.2f}% ({total_usage} cgroup time)"
        )


class MemoryUsageLogger(UsageLoggerInterface):
    BYTES_PER_MB = 1 << 20

    def __init__(self, logger: logging.LoggerAdapter, cgroup: str):
        self._logger = logger
        memory_root = f"{CGROUPFS_ROOT}/{cgroup}memory"
        self._memory_usage = Path(os.path.join(memory_root, "memory.usage_in_bytes"))
        self._memory_wm = Path(os.path.join(memory_root, "memory.max_usage_in_bytes"))
        self._last_usage: Optional[int] = None
        self._last_wm: Optional[float] = None

    def _read_cgroup_memory_usage(self) -> Tuple[int, int]:
        """
        Reads the current usage & max_usage for a cgroup.
        """
        return int(self._memory_usage.read_text()), int(self._memory_wm.read_text())

    def init_cycles(self):
        self._last_usage, self._last_wm = self._read_cgroup_memory_usage()

    def log_cycle(self):
        assert self._last_usage is not None and self._last_wm is not None, "didn't call init_cycles()?"

        now_usage, now_wm = self._read_cgroup_memory_usage()

        diff_usage = (now_usage - self._last_usage) / self.BYTES_PER_MB
        diff_usage_str = f"diff {diff_usage:+.3f} MB" if diff_usage != 0 else "no diff"
        diff_wm = (now_wm - self._last_wm) / self.BYTES_PER_MB
        diff_wm_str = f"diff {diff_wm:+.3f} MB" if diff_wm != 0 else "no diff"

        self._logger.debug(
            f"Memory usage: {now_usage / self.BYTES_PER_MB:.3f} MB ({diff_usage_str}),"
            f" watermark is {now_wm / self.BYTES_PER_MB:.3f} MB ({diff_wm_str})"
        )

        self._last_usage = now_usage
        self._last_wm = now_wm

    def log_run(self):
        now_usage, now_wm = self._read_cgroup_memory_usage()

        self._logger.debug(
            f"Final memory usage this run: {now_usage / self.BYTES_PER_MB:.3f} MB,"
            f" watermark is {now_wm / self.BYTES_PER_MB:.3f} MB"
        )


class CgroupsUsageLogger(UsageLoggerInterface):
    def __init__(self, logger: logging.LoggerAdapter, cgroup: str):
        self._cpu_logger = CpuUsageLogger(logger, cgroup)
        self._memory_logger = MemoryUsageLogger(logger, cgroup)

    def init_cycles(self):
        self._cpu_logger.init_cycles()
        self._memory_logger.init_cycles()

    def log_cycle(self):
        self._cpu_logger.log_cycle()
        self._memory_logger.log_cycle()

    def log_run(self):
        self._cpu_logger.log_run()
        self._memory_logger.log_run()


class NoopUsageLogger(UsageLoggerInterface):
    def init_cycles(self):
        pass

    def log_cycle(self):
        pass

    def log_run(self):
        pass
