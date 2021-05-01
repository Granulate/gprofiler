#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from typing import Mapping


class ProfilerBase:
    """
    Base profiler class for all profilers.
    """

    def start(self) -> None:
        pass

    def snapshot(self) -> Mapping[int, Mapping[str, int]]:
        """
        :returns: Mapping from pid to stacks and their counts.
        """
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class NoopProfiler(ProfilerBase):
    """
    No-op profiler - used as a drop-in replacement for runtime profilers, when they are disabled.
    """

    def snapshot(self) -> Mapping[int, Mapping[str, int]]:
        return {}
