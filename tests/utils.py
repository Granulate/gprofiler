#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import platform
import re
import subprocess
from contextlib import contextmanager
from logging import LogRecord
from pathlib import Path
from threading import Event
from time import sleep
from typing import Any, Dict, Iterator, List, Optional

from docker import DockerClient
from docker.errors import ContainerError
from docker.models.containers import Container
from docker.models.images import Image
from docker.types import Mount
from psutil import Process

from gprofiler.gprofiler_types import ProfileData, StackToSampleCount
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.java import (
    JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE,
    JAVA_SAFEMODE_ALL,
    AsyncProfiledProcess,
    JavaFlagCollectionOptions,
    JavaProfiler,
)
from gprofiler.profilers.profiler_base import ProfilerInterface
from gprofiler.utils import remove_path, wait_event

RUNTIME_PROFILERS = [
    ("java", "ap"),
    ("python", "py-spy"),
    ("python", "pyperf"),
    ("php", "phpspy"),
    ("ruby", "rbspy"),
    ("nodejs", "perf"),
]


def start_container(
    docker_client: DockerClient,
    image: Image,
    command: Optional[List[str]] = None,
    volumes: Dict[str, Dict[str, str]] = None,
    privileged: bool = False,
    pid_mode: Optional[str] = None,
    **extra_kwargs: Any,
) -> Container:
    if volumes is None:
        volumes = {}

    return docker_client.containers.run(
        image,
        command,
        privileged=privileged,
        network_mode="host",
        pid_mode=pid_mode,
        userns_mode="host",
        volumes=volumes,
        labels=[docker_client._gprofiler_test_id],
        stderr=True,
        detach=True,
        **extra_kwargs,
    )


# offset doesn't have a default value so that you don't forget it.
def wait_for_log(container: Container, log: str, offset: int, timeout: int = 60) -> int:
    def find_in_logs() -> Optional[int]:
        m = re.search(log.encode(), container.logs(), re.DOTALL)
        if m is not None:
            return m.start()
        return None

    try:
        wait_event(timeout, Event(), lambda: find_in_logs() is not None)
        ofs = find_in_logs()
        assert ofs is not None
        return ofs
    except TimeoutError:
        print(container.logs())
        raise


def wait_for_container(container: Container) -> str:
    exit_status = container.wait()["StatusCode"]
    logs = container.logs(stdout=True, stderr=True)
    assert isinstance(logs, bytes), logs

    if exit_status != 0:
        raise ContainerError(container, exit_status, container.attrs["Config"]["Cmd"], container.image, logs)

    # print, so failing tests display it
    print(
        "Container logs:",
        logs.decode() if len(logs) > 0 else "(empty, possibly because container was detached and is running now)",
    )

    return logs.decode()


def run_privileged_container(
    docker_client: DockerClient,
    image: Image,
    command: List[str],
    volumes: Dict[str, Dict[str, str]] = None,
    **extra_kwargs: Any,
) -> str:
    container = None
    try:
        container = start_container(
            docker_client, image, command, volumes, pid_mode="host", privileged=True, **extra_kwargs
        )
        return wait_for_container(container)

    finally:
        if container is not None:
            container.remove()


def _no_errors(logs: str) -> None:
    # example line: [2021-06-12 10:13:57,528] ERROR: gprofiler: ruby profiling failed
    assert "] ERROR: " not in logs, f"found ERRORs in gProfiler logs!: {logs}"


def run_gprofiler_in_container(docker_client: DockerClient, image: Image, command: List[str], **kwargs: Any) -> None:
    """
    Wrapper around run_privileged_container() that also verifies there are no ERRORs in gProfiler's output log.
    """
    assert "-v" in command, "please run with -v!"  # otherwise there are no loglevel prints
    logs = run_privileged_container(docker_client, image, command, **kwargs)
    _no_errors(logs)


def copy_file_from_image(image: Image, container_path: str, host_path: str) -> None:
    os.makedirs(os.path.dirname(host_path), exist_ok=True)
    # I tried writing it with the docker-py API, but retrieving large files with container.get_archive() just hangs...
    subprocess.run(
        f"c=$(docker container create {image.id}) && "
        f"{{ docker cp $c:{container_path} {host_path}; ret=$?; docker rm $c > /dev/null; exit $ret; }}",
        shell=True,
        check=True,
    )


def chmod_path_parts(path: Path, add_mode: int) -> None:
    """
    Adds 'add_mode' to all parts in 'path'.
    """
    assert os.path.isabs(path), f"absolute path required: {path}"
    for i in range(1, len(path.parts)):
        subpath = os.path.join(*path.parts[: i + 1])
        if subpath == "/":
            # skip chmodding /
            continue
        os.chmod(subpath, os.stat(subpath).st_mode | add_mode)


def is_function_in_collapsed(function_name: str, collapsed: StackToSampleCount) -> bool:
    return any((function_name in record) for record in collapsed.keys())


def is_pattern_in_collapsed(pattern: str, collapsed: StackToSampleCount) -> bool:
    regex = re.compile(pattern, re.IGNORECASE)
    return any(regex.search(record) is not None for record in collapsed.keys())


def is_aarch64() -> bool:
    return platform.machine() == "aarch64"


def assert_function_in_collapsed(function_name: str, collapsed: StackToSampleCount) -> None:
    print(f"collapsed: {collapsed}")
    assert is_function_in_collapsed(function_name, collapsed), f"function {function_name!r} missing in collapsed data!"


def assert_ldd_version_container(container: Container, version: str) -> None:
    exec_output = container.exec_run("ldd --version").output.decode("utf-8")
    search_result = re.search(r"^ldd \(.*\) (\d*.\d*)", exec_output)
    if search_result:
        version_in_container = search_result.group(1)
    else:
        version_in_container = None
    assert version_in_container == version, f"ldd version in container: {version_in_container}, expected {version}"


def snapshot_pid_profile(profiler: ProfilerInterface, pid: int) -> ProfileData:
    return profiler.snapshot()[pid]


def snapshot_pid_collapsed(profiler: ProfilerInterface, pid: int) -> StackToSampleCount:
    return snapshot_pid_profile(profiler, pid).stacks


def make_java_profiler(
    profiler_state: ProfilerState,
    frequency: int = 11,
    duration: int = 1,
    java_version_check: bool = True,
    java_async_profiler_mode: str = "cpu",
    java_async_profiler_safemode: int = JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE,
    java_async_profiler_args: str = "",
    java_safemode: str = JAVA_SAFEMODE_ALL,
    java_jattach_timeout: int = AsyncProfiledProcess._DEFAULT_JATTACH_TIMEOUT,
    java_async_profiler_mcache: int = AsyncProfiledProcess._DEFAULT_MCACHE,
    java_async_profiler_report_meminfo: bool = True,
    java_collect_spark_app_name_as_appid: bool = False,
    java_mode: str = "ap",
    java_collect_jvm_flags: str = JavaFlagCollectionOptions.DEFAULT,
    java_full_hserr: bool = False,
    java_include_method_modifiers: bool = False,
) -> JavaProfiler:
    return JavaProfiler(
        frequency=frequency,
        duration=duration,
        profiler_state=profiler_state,
        java_version_check=java_version_check,
        java_async_profiler_mode=java_async_profiler_mode,
        java_async_profiler_safemode=java_async_profiler_safemode,
        java_async_profiler_args=java_async_profiler_args,
        java_safemode=java_safemode,
        java_jattach_timeout=java_jattach_timeout,
        java_async_profiler_mcache=java_async_profiler_mcache,
        java_collect_spark_app_name_as_appid=java_collect_spark_app_name_as_appid,
        java_mode=java_mode,
        java_async_profiler_report_meminfo=java_async_profiler_report_meminfo,
        java_collect_jvm_flags=java_collect_jvm_flags,
        java_full_hserr=java_full_hserr,
        java_include_method_modifiers=java_include_method_modifiers,
    )


def start_gprofiler_in_container_for_one_session(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_path: Path,
    runtime_specific_args: List[str],
    profiler_flags: List[str],
    privileged: bool = True,
    user: int = 0,
    pid_mode: Optional[str] = "host",
) -> Container:
    inner_output_directory = "/tmp/gprofiler"
    volumes = {
        str(output_directory): {"bind": inner_output_directory, "mode": "rw"},
    }
    args = ["-v", "-d", "3", "-o", inner_output_directory] + runtime_specific_args + profiler_flags

    remove_path(str(output_path), missing_ok=True)
    return start_container(
        docker_client,
        gprofiler_docker_image,
        args,
        privileged=privileged,
        volumes=volumes,
        user=user,
        pid_mode=pid_mode,
    )


def wait_for_gprofiler_container(container: Container, output_path: Path) -> str:
    """
    Wrapper around wait_for_container() that also verifies there are not ERRORs in gProfiler's output log.
    """
    logs = wait_for_container(container)
    _no_errors(logs)
    return output_path.read_text()


def run_gprofiler_in_container_for_one_session(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_path: Path,
    runtime_specific_args: List[str],
    profiler_flags: List[str],
) -> str:
    """
    Runs the gProfiler container image for a single profiling session, and collects the output.
    """
    container: Container = None
    try:
        container = start_gprofiler_in_container_for_one_session(
            docker_client, gprofiler_docker_image, output_directory, output_path, runtime_specific_args, profiler_flags
        )
        return wait_for_gprofiler_container(container, output_path)
    finally:
        if container is not None:
            container.remove()


def _print_process_output(popen: subprocess.Popen) -> None:
    stdout, stderr = popen.communicate()
    print(f"stdout: {stdout.decode()}")
    print(f"stderr: {stderr.decode()}")


@contextmanager
def _application_process(command_line: List[str], check_app_exited: bool) -> Iterator[subprocess.Popen]:
    # run as non-root to catch permission errors, etc.
    def lower_privs() -> None:
        os.setgid(1000)
        os.setuid(1000)

    popen = subprocess.Popen(
        command_line, preexec_fn=lower_privs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/tmp"
    )
    try:
        # wait 2 seconds to ensure it starts
        popen.wait(2)
    except subprocess.TimeoutExpired:
        pass
    else:
        _print_process_output(popen)
        raise Exception(f"Command {command_line} exited unexpectedly with {popen.returncode}")

    yield popen

    if check_app_exited:
        # ensure, again, that it still alive (if it exited prematurely it might provide bad data for the tests)
        try:
            popen.wait(0)
        except subprocess.TimeoutExpired:
            pass
        else:
            _print_process_output(popen)
            raise Exception(f"Command {command_line} exited unexpectedly during the test with {popen.returncode}")

    popen.kill()
    _print_process_output(popen)


def wait_container_to_start(container: Container) -> None:
    while container.status != "running":
        if container.status == "exited":
            raise Exception(container.logs().decode())
        sleep(1)
        container.reload()


@contextmanager
def _application_docker_container(
    docker_client: DockerClient,
    application_docker_image: Image,
    application_docker_mounts: List[Mount],
    application_docker_capabilities: List[str],
    application_docker_command: Optional[List[str]] = None,
) -> Container:
    container: Container = start_container(
        docker_client,
        application_docker_image,
        application_docker_command,
        user="5555:6666",
        mounts=application_docker_mounts,
        cap_add=application_docker_capabilities,
    )
    wait_container_to_start(container)
    yield container
    container.remove(force=True)


def find_application_pid(pid: int) -> int:
    # Application might be run using "sh -c ...", we detect the case and return the "real" application pid
    process = Process(pid)
    if (
        process.cmdline()[0].endswith("sh")
        and process.cmdline()[1] == "-c"
        and len(process.children(recursive=False)) == 1
    ):
        pid = process.children(recursive=False)[0].pid

    return pid


def assert_jvm_flags_equal(actual_jvm_flags: Optional[List], expected_jvm_flags: List) -> None:
    """
    When comparing JVM flags, we want to ignore the "value" field
    as it might change in the CI due to ergonomic set flags
    """
    assert actual_jvm_flags is not None, f"{actual_jvm_flags} != {expected_jvm_flags}"

    assert len(actual_jvm_flags) == len(expected_jvm_flags), f"{actual_jvm_flags} != {expected_jvm_flags}"

    for actual_flag_dict, expected_flag_dict in zip(actual_jvm_flags, expected_jvm_flags):
        actual_flag_value = actual_flag_dict.pop("value")
        expected_flag_value = expected_flag_dict.pop("value")

        if expected_flag_value is not None:
            assert (
                actual_flag_value == expected_flag_value
            ), f"{actual_flag_dict|{'value': actual_flag_value}} != {expected_flag_dict|{'value': expected_flag_value}}"

        assert actual_flag_dict == expected_flag_dict


def log_record_extra(r: LogRecord) -> Dict[Any, Any]:
    """
    Gets the "extra" attached to a LogRecord
    """
    return getattr(r, "extra", {})
