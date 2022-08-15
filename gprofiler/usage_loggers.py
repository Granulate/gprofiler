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
    def init_cycles(self) -> None:
        raise NotImplementedError

    def log_cycle(self) -> None:
        raise NotImplementedError

    def log_run(self) -> None:
        raise NotImplementedError


class CpuUsageLogger(UsageLoggerInterface):
    NSEC_PER_SEC = 1000000000

    def __init__(self, logger: logging.LoggerAdapter, cgroup: str):
        self._logger = logger
        self._cpuacct_usage = Path(f"{CGROUPFS_ROOT}{cgroup}cpuacct/cpuacct.usage")
        self._last_usage: Optional[int] = None
        self._last_ts: Optional[float] = None

    def _read_cgroup_cpu_usage(self) -> int:
        """
        Reads the current snapshot of cpuacct.usage for a cgroup.
        """
        return int(self._cpuacct_usage.read_text())

    def init_cycles(self) -> None:
        self._last_usage = self._read_cgroup_cpu_usage()
        self._last_ts = time.monotonic()

    def log_cycle(self) -> None:
        assert self._last_usage is not None and self._last_ts is not None, "didn't call init_cycles()?"

        current_usage = self._read_cgroup_cpu_usage()
        current_ts = time.monotonic()

        diff_usage = current_usage - self._last_usage
        diff_usage_s = diff_usage / self.NSEC_PER_SEC
        diff_ts = current_ts - self._last_ts

        self._logger.debug(
            f"CPU usage this cycle: {diff_usage_s:.3f}"
            f" seconds {diff_usage_s / diff_ts * 100:.2f}% ({diff_usage} cgroup time)"
        )

        self._last_usage = current_usage
        self._last_ts = current_ts

    def log_run(self) -> None:
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
        memory_root = f"{CGROUPFS_ROOT}{cgroup}memory"
        self._memory_usage = Path(os.path.join(memory_root, "memory.usage_in_bytes"))
        self._memory_watermark = Path(os.path.join(memory_root, "memory.max_usage_in_bytes"))
        self._last_usage: Optional[int] = None
        self._last_watermark: Optional[float] = None

    def _read_cgroup_memory_usage(self) -> Tuple[int, int]:
        """
        Reads the current usage & max_usage for a cgroup.
        """
        return int(self._memory_usage.read_text()), int(self._memory_watermark.read_text())

    def init_cycles(self) -> None:
        self._last_usage, self._last_watermark = self._read_cgroup_memory_usage()

    def log_cycle(self) -> None:
        assert self._last_usage is not None and self._last_watermark is not None, "didn't call init_cycles()?"

        current_usage, current_watermark = self._read_cgroup_memory_usage()

        diff_usage = (current_usage - self._last_usage) / self.BYTES_PER_MB
        diff_usage_str = f"diff {diff_usage:+.3f} MB" if diff_usage != 0 else "no diff"
        diff_watermark = (current_watermark - self._last_watermark) / self.BYTES_PER_MB
        diff_watermark_str = f"diff {diff_watermark:+.3f} MB" if diff_watermark != 0 else "no diff"

        self._logger.debug(
            f"Memory usage: {current_usage / self.BYTES_PER_MB:.3f} MB ({diff_usage_str}),"
            f" watermark is {current_watermark / self.BYTES_PER_MB:.3f} MB ({diff_watermark_str})"
        )

        self._last_usage = current_usage
        self._last_watermark = current_watermark

    def log_run(self) -> None:
        current_usage, current_watermark = self._read_cgroup_memory_usage()

        self._logger.debug(
            f"Final memory usage this run: {current_usage / self.BYTES_PER_MB:.3f} MB,"
            f" watermark is {current_watermark / self.BYTES_PER_MB:.3f} MB"
        )


class CgroupsUsageLogger(UsageLoggerInterface):
    def __init__(self, logger: logging.LoggerAdapter, cgroup: str):
        assert cgroup.startswith("/"), f"cgroup {cgroup} must start with a /"
        self._cpu_logger = CpuUsageLogger(logger, cgroup)
        self._memory_logger = MemoryUsageLogger(logger, cgroup)

    def init_cycles(self) -> None:
        self._cpu_logger.init_cycles()
        self._memory_logger.init_cycles()

    def log_cycle(self) -> None:
        self._cpu_logger.log_cycle()
        self._memory_logger.log_cycle()

    def log_run(self) -> None:
        self._cpu_logger.log_run()
        self._memory_logger.log_run()


class NoopUsageLogger(UsageLoggerInterface):
    def init_cycles(self) -> None:
        pass

    def log_cycle(self) -> None:
        pass

    def log_run(self) -> None:
        pass
