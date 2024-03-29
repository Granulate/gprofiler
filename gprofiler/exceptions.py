#
# Copyright (C) 2022 Intel Corporation
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
import signal
import subprocess
from typing import List, Union


class PerfNoSupportedEvent(Exception):
    pass


class StopEventSetException(Exception):
    pass


class ProcessStoppedException(Exception):
    pass


class CalledProcessError(subprocess.CalledProcessError):
    # Enough characters for 200 long lines
    MAX_STDIO_LENGTH = 120 * 200

    def __init__(
        self,
        returncode: int,
        cmd: Union[str, List[str]],
        output: str,
        stderr: str,
    ):
        assert isinstance(returncode, int), returncode
        assert isinstance(cmd, str) or all(isinstance(s, str) for s in cmd), cmd
        assert output is None or isinstance(output, str), output
        assert stderr is None or isinstance(stderr, str), stderr
        super().__init__(returncode, cmd, output, stderr)

    def _truncate_stdio(self, stdio: str) -> str:
        if len(stdio) > self.MAX_STDIO_LENGTH:
            stdio = stdio[: self.MAX_STDIO_LENGTH - 3] + "..."
        return stdio

    def __str__(self) -> str:
        if self.returncode and self.returncode < 0:
            try:
                base = f"Command {self.cmd!r} died with {signal.Signals(-self.returncode)!r}."
            except ValueError:
                base = f"Command {self.cmd!r} died with unknown signal {-self.returncode}."
        else:
            base = f"Command {self.cmd!r} returned non-zero exit status {self.returncode}."
        return f"{base}\nstdout: {self._truncate_stdio(self.stdout)}\nstderr: {self._truncate_stdio(self.stderr)}"


class CalledProcessTimeoutError(CalledProcessError):
    def __init__(
        self,
        timeout: float,
        returncode: int,
        cmd: Union[str, List[str]],
        output: str,
        stderr: str,
    ):
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


class ThreadStopTimeoutError(Exception):
    pass


class SystemProfilerStartFailure(Exception):
    pass


class NoProfilersEnabledError(Exception):
    pass


class NoRwExecDirectoryFoundError(Exception):
    pass
