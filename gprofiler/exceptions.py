#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import signal
import subprocess
from typing import List, Union, Any


class StopEventSetException(Exception):
    pass


class ProcessStoppedException(Exception):
    pass


class CalledProcessError(subprocess.CalledProcessError):
    def __str__(self) -> str:
        if self.returncode and self.returncode < 0:
            try:
                base = f"Command '{self.cmd}' died with {signal.Signals(-self.returncode)!r}."
            except ValueError:
                base = f"Command '{self.cmd}' died with unknown signal {-self.returncode}."
        else:
            base = f"Command '{self.cmd}' returned non-zero exit status {self.returncode}. "
        return f"{base}\nstdout: {self.stdout}\nstderr: {self.stderr}"


class CalledProcessTimeoutError(CalledProcessError):
    def __init__(self, timeout: float, returncode: int, cmd: Union[str, List[str]], output: Any = None,
                 stderr: Any = None):
        super().__init__(returncode, cmd, output, stderr)
        self.timeout = timeout

    def __str__(self) -> str:
        return f"Timed out after {self.timeout} seconds\n" + super().__str__()


class ProgramMissingException(Exception):
    def __init__(self, program: str):
        super().__init__(f"The program {program!r} is missing! Please install it")


class APIError(Exception):
    def __init__(self, message: str, full_data: dict = None):
        self.message = message
        self.full_data = full_data

    def __str__(self) -> str:
        return self.message


class UninitializedStateException(Exception):
    pass


class StateAlreadyInitializedException(Exception):
    pass


class BadResponseCode(Exception):
    def __init__(self, response_code: int):
        super().__init__(f"Got a bad HTTP response code {response_code}")


class ThreadStopTimeoutError(Exception):
    pass


class SystemProfilerInitFailure(Exception):
    pass
