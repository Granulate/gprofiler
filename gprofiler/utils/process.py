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
import contextlib
import re
from typing import Callable, Iterator, Match, Optional

from granulate_utils.linux.process import is_process_running, read_proc_file
from psutil import AccessDenied, NoSuchProcess, Process, process_iter

from gprofiler.platform import is_windows


def search_proc_maps(process: Process, pattern: str) -> Optional[Match[str]]:
    return re.search(pattern, read_proc_file(process, "maps").decode(), re.MULTILINE)


def process_comm(process: Process) -> str:
    if is_windows():
        return process.name()
    else:
        status = read_proc_file(process, "status").decode()
        name_line = status.splitlines()[0]
        assert name_line.startswith("Name:\t")
        return name_line.split("\t", 1)[1]


def search_for_process(filter: Callable[[Process], bool]) -> Iterator[Process]:
    for proc in process_iter():
        with contextlib.suppress(NoSuchProcess, AccessDenied):
            if is_process_running(proc) and filter(proc):
                yield proc
