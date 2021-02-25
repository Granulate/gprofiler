#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import signal
import subprocess


class StopEventSetException(Exception):
    pass


class ProcessStoppedException(Exception):
    pass


class CalledProcessError(subprocess.CalledProcessError):
    def __str__(self):
        if self.returncode and self.returncode < 0:
            try:
                return f"Command '{self.cmd}' died with {signal.Signals(-self.returncode)!r}."
            except ValueError:
                return f"Command '{self.cmd}' died with unknown signal {-self.returncode}."
        else:
            return (
                f"Command '{self.cmd}' returned non-zero exit status {self.returncode}. "
                f"stderr: {self.stderr}, stdout: {self.stdout}"
            )
