#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import os
import re
import signal
from functools import lru_cache
from typing import Any, Match, Optional, Union, List, Tuple
from subprocess import CompletedProcess, Popen, TimeoutExpired
from threading import Event

from granulate_utils.linux.process import process_exe, read_proc_file
from psutil import Process
from gprofiler.utils import get_staticx_dir


def search_proc_maps(process: Process, pattern: str) -> Optional[Match[str]]:
    return re.search(pattern, read_proc_file(process, "maps").decode(), re.MULTILINE)


def process_comm(process: Process) -> str:
    status = read_proc_file(process, "status").decode()
    name_line = status.splitlines()[0]
    assert name_line.startswith("Name:\t")
    return name_line.split("\t", 1)[1]


@lru_cache(maxsize=512)
def is_process_basename_matching(process: Process, basename_pattern: str) -> bool:
    if re.match(basename_pattern, os.path.basename(process_exe(process))):
        return True

    # process was executed AS basename (but has different exe name)
    cmd = process.cmdline()
    if len(cmd) > 0 and re.match(basename_pattern, cmd[0]):
        return True

    return False

def start_process(
    cmd: Union[str, List[str]], via_staticx: bool, term_on_parent_death: bool = True, **kwargs: Any
) -> Popen:
    cmd_text = " ".join(cmd) if isinstance(cmd, list) else cmd
    logger.debug(f"Running command: ({cmd_text})")
    if isinstance(cmd, str):
        cmd = [cmd]

    env = kwargs.pop("env", None)
    staticx_dir = get_staticx_dir()
    # are we running under staticx?
    if staticx_dir is not None:
        # if so, if "via_staticx" was requested, then run the binary with the staticx ld.so
        # because it's supposed to be run with it.
        if via_staticx:
            # staticx_dir (from STATICX_BUNDLE_DIR) is where staticx has extracted all of the
            # libraries it had collected earlier.
            # see https://github.com/JonathonReinhart/staticx#run-time-information
            cmd = [f"{staticx_dir}/.staticx.interp", "--library-path", staticx_dir] + cmd
        else:
            # explicitly remove our directory from LD_LIBRARY_PATH
            env = env if env is not None else os.environ.copy()
            env.update({"LD_LIBRARY_PATH": ""})

    cur_preexec_fn = kwargs.pop("preexec_fn", os.setpgrp)

    if term_on_parent_death:
        cur_preexec_fn = wrap_callbacks([set_child_termination_on_parent_death, cur_preexec_fn])

    popen = Popen(
        cmd,
        stdout=kwargs.pop("stdout", subprocess.PIPE),
        stderr=kwargs.pop("stderr", subprocess.PIPE),
        stdin=subprocess.PIPE,
        preexec_fn=cur_preexec_fn,
        env=env,
        **kwargs,
    )
    return popen

def poll_process(process: Popen, timeout: float, stop_event: Event) -> None:
    try:
        wait_event(timeout, stop_event, lambda: process.poll() is not None)
    except StopEventSetException:
        process.kill()
        raise

def _reap_process(process: Popen, kill_signal: signal.Signals) -> Tuple[int, str, str]:
    # kill the process and read its output so far
    process.send_signal(kill_signal)
    process.wait()
    logger.debug(f"({process.args!r}) was killed by us with signal {kill_signal} due to timeout or stop request")
    stdout, stderr = process.communicate()
    returncode = process.poll()
    assert returncode is not None  # only None if child has not terminated
    return returncode, stdout, stderr

def run_process(
    cmd: Union[str, List[str]],
    stop_event: Event = None,
    suppress_log: bool = False,
    via_staticx: bool = False,
    check: bool = True,
    timeout: int = None,
    kill_signal: signal.Signals = signal.SIGKILL,
    communicate: bool = True,
    stdin: bytes = None,
    **kwargs: Any,
) -> "CompletedProcess[bytes]":
    stdout = None
    stderr = None
    reraise_exc: Optional[BaseException] = None
    with start_process(cmd, via_staticx, **kwargs) as process:
        try:
            communicate_kwargs = dict(input=stdin) if stdin is not None else {}
            if stop_event is None:
                assert timeout is None, f"expected no timeout, got {timeout!r}"
                if communicate:
                    # wait for stderr & stdout to be closed
                    stdout, stderr = process.communicate(timeout=timeout, **communicate_kwargs)
                else:
                    # just wait for the process to exit
                    process.wait()
            else:
                end_time = (time.monotonic() + timeout) if timeout is not None else None
                while True:
                    try:
                        if communicate:
                            stdout, stderr = process.communicate(timeout=1, **communicate_kwargs)
                        else:
                            process.wait(timeout=1)
                        break
                    except TimeoutExpired:
                        if stop_event.is_set():
                            raise ProcessStoppedException from None
                        if end_time is not None and time.monotonic() > end_time:
                            assert timeout is not None
                            raise
        except TimeoutExpired:
            returncode, stdout, stderr = _reap_process(process, kill_signal)
            assert timeout is not None
            reraise_exc = CalledProcessTimeoutError(timeout, returncode, cmd, stdout, stderr)
        except BaseException as e:  # noqa
            returncode, stdout, stderr = _reap_process(process, kill_signal)
            reraise_exc = e
        retcode = process.poll()
        assert retcode is not None  # only None if child has not terminated

    result: CompletedProcess[bytes] = CompletedProcess(process.args, retcode, stdout, stderr)

    logger.debug(f"({process.args!r}) exit code: {result.returncode}")
    if not suppress_log:
        if result.stdout:
            logger.debug(f"({process.args!r}) stdout: {result.stdout.decode()!r}")
        if result.stderr:
            logger.debug(f"({process.args!r}) stderr: {result.stderr.decode()!r}")
    if reraise_exc is not None:
        raise reraise_exc
    elif check and retcode != 0:
        raise CalledProcessError(retcode, process.args, output=stdout, stderr=stderr)
    return result


def pgrep_exe(match: str) -> List[Process]:
    pattern = re.compile(match)
    procs = []
    for process in psutil.process_iter():
        try:
            # kernel threads should be child of process with pid 2
            if process.pid != 2 and process.ppid() != 2 and pattern.match(process_exe(process)):
                procs.append(process)
        except psutil.NoSuchProcess:  # process might have died meanwhile
            continue
    return procs


def pgrep_maps(match: str) -> List[Process]:
    # this is much faster than iterating over processes' maps with psutil.
    result = run_process(
        f"grep -lP '{match}' /proc/*/maps",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        suppress_log=True,
        check=False,
    )
    # 0 - found
    # 1 - not found
    # 2 - error (which we might get for a missing /proc/pid/maps file of a process which just exited)
    # so this ensures grep wasn't killed by a signal
    assert result.returncode in (
        0,
        1,
        2,
    ), f"unexpected 'grep' exit code: {result.returncode}, stdout {result.stdout!r} stderr {result.stderr!r}"

    error_lines = []
    for line in result.stderr.splitlines():
        if not (
            line.startswith(b"grep: /proc/")
            and (line.endswith(b"/maps: No such file or directory") or line.endswith(b"/maps: No such process"))
        ):
            error_lines.append(line)
    if error_lines:
        logger.error(f"Unexpected 'grep' error output (first 10 lines): {error_lines[:10]}")

    processes: List[Process] = []
    for line in result.stdout.splitlines():
        assert line.startswith(b"/proc/") and line.endswith(b"/maps"), f"unexpected 'grep' line: {line!r}"
        pid = int(line[len(b"/proc/") : -len(b"/maps")])
        try:
            processes.append(Process(pid))
        except psutil.NoSuchProcess:
            continue  # process might have died meanwhile

    return processes