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
                base = f"Command '{self.cmd}' died with {signal.Signals(-self.returncode)!r}."
            except ValueError:
                base = f"Command '{self.cmd}' died with unknown signal {-self.returncode}."
        else:
            base = f"Command '{self.cmd}' returned non-zero exit status {self.returncode}. "
        return f"{base}\nstdout: {self.stdout}\nstderr: {self.stderr}"


class ProgramMissingException(Exception):
    def __init__(self, program: str):
        super().__init__(f"The program {program!r} is missing! Please install it")
