from functools import lru_cache
import shutil
import traceback
from typing import Callable
from gprofiler.metadata import get_exe_version
from gprofiler.log import get_logger_adapter
from granulate_utils.linux.ns import get_proc_root_path, is_same_ns, NsType, run_in_ns
from granulate_utils.linux.process import is_musl


from . import add_permission_dir, pgrep_maps, resource_path, pgrep_exe

import json
import requests
import os
import signal
import psutil
import stat

from retry import retry
from websocket import create_connection
from websocket._core import WebSocket

logger = get_logger_adapter(__name__)


class NodeDebuggerUrlNotFound(Exception):
    pass


class NodeDebuggerUnexpectedResponse(Exception):
    pass


def _start_debugger(pid):
    os.kill(pid, signal.SIGUSR1)


def get_node_processes():
    return pgrep_maps(r"(?:^.+/node[^/]*$)")


@retry(NodeDebuggerUrlNotFound, 5, 1)
def _get_debugger_url() -> str:
    # when killing process with SIGUSR1 it will open new debugger session on port 9229,
    # so it will always the same
    port = 9229
    debugger_url_response = requests.get(f"http://127.0.0.1:{port}/json/list")
    if (
        debugger_url_response.status_code != 200
        or not "application/json" in debugger_url_response.headers.get("Content-Type")
    ):
        raise NodeDebuggerUrlNotFound

    response_json = debugger_url_response.json()
    if (
        not isinstance(response_json, list)
        or len(response_json) == 0
        or not isinstance(response_json[0], dict)
        or not "webSocketDebuggerUrl" in response_json[0].keys()
    ):
        raise NodeDebuggerUrlNotFound

    return response_json[0]["webSocketDebuggerUrl"]


@retry(NodeDebuggerUnexpectedResponse, 5, 1)
def _load_dso(sock: WebSocket, module_path: str):
    cdp_request = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": f'process.mainModule.require("{os.path.join(module_path, "linux-perf.js")}").start()',
            "replMode": True,
        },
    }
    sock.send(json.dumps(cdp_request))
    message = sock.recv()
    try:
        message = json.loads(message)

    except json.JSONDecodeError:
        raise NodeDebuggerUnexpectedResponse(message)

    if (
        "result" not in message.keys()
        or "result" not in message["result"].keys()
        or "type" not in message["result"]["result"].keys()
        or message["result"]["result"]["type"] != "boolean"
    ):
        raise NodeDebuggerUnexpectedResponse(message)


@retry(NodeDebuggerUnexpectedResponse, 5, 1)
def _stop_dso(sock: WebSocket, module_path: str):
    cdp_request = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": f'process.mainModule.require("{os.path.join(module_path, "linux-perf.js")}").stop()',
            "replMode": True,
        },
    }
    sock.send(json.dumps(cdp_request))
    message = sock.recv()
    try:
        message = json.loads(message)
    except json.JSONDecodeError:
        raise NodeDebuggerUnexpectedResponse(message)

    if (
        "result" not in message.keys()
        or "result" not in message["result"].keys()
        or "type" not in message["result"]["result"].keys()
        or message["result"]["result"]["type"] != "boolean"
    ):
        raise NodeDebuggerUnexpectedResponse(message)


def _copy_module_into_process_ns(process: psutil.Process, musl: bool, version: str):
    proc_root = get_proc_root_path(process)
    libc = "musl" if musl else "glibc"
    dest_inside_container = os.path.join("tmp", "node_module", libc, version)
    dest = os.path.join(proc_root, dest_inside_container)
    if os.path.exists(dest):
        return "/" + dest_inside_container
    src = resource_path(os.path.join("node", "module", libc, version))
    shutil.copytree(src, dest)
    add_permission_dir(dest, stat.S_IROTH, stat.S_IXOTH | stat.S_IROTH)
    return "/" + dest_inside_container


def _create_symlink_for_map(process: psutil.Process, pid_inside_ns: int):
    proc_root = get_proc_root_path(process)
    map_file_name = f"perf-{process.pid}.map"
    src = os.path.join(proc_root, "tmp", f"perf-{pid_inside_ns}.map")
    dest = os.path.join("/tmp", map_file_name)
    os.symlink(src, dest)


@lru_cache(1024)
def _get_pid_inside_ns(pid: int) -> int:
    with open(f"/proc/{pid}/status") as f:
        for line in f.readlines():
            if line.startswith("NSpid"):
                return line.split("\t")[-1].strip("\n")


def _generate_perf_map_wrapper(module_path: str) -> Callable:
    def generate_perf_map():
        debugger_url = _get_debugger_url()
        sock = create_connection(debugger_url)
        _load_dso(sock, module_path)

    return generate_perf_map


def _clean_up_wrapper(module_path: str, pid: int) -> Callable:
    def clean_up():
        debugger_url = _get_debugger_url()
        sock = create_connection(debugger_url)
        _stop_dso(sock, module_path)
        shutil.rmtree(os.path.join("/tmp", "node_module"))
        os.remove(os.path.join("/tmp", f"perf-{pid}.map"))

    return clean_up


def generate_map_for_node_processes():
    """Iterates over all NodeJS processes, starts debugger for it, finds debugger URL,
    copies node-linux-perf module into process' namespace, loads module and starts it.
    After that it links it into gProfiler namespace's /tmp. This lets perf load it"""
    processes = get_node_processes()
    for process in processes:
        try:
            musl = is_musl(process)
            node_version = get_exe_version(process)
            node_major_version = node_version[1:].split(".")[0]
            dest = _copy_module_into_process_ns(process, musl, node_major_version)
            _start_debugger(process.pid)
            pid_inside_ns = _get_pid_inside_ns(process.pid)
            cp = run_in_ns(
                ["pid", "mnt", "net"], _generate_perf_map_wrapper(dest), process.pid
            )
            if hasattr(cp, "stdout"):
                logger.debug(cp.stdout.decode().strip())
            if hasattr(cp, "stderr"):
                logger.debug(cp.stderr.decode().strip())
            if not is_same_ns(process, NsType.mnt.name):
                _create_symlink_for_map(process, pid_inside_ns)
        except Exception as e:
            logger.warn(
                f"Could not create debug symbols for pid {process.pid}. Reason: {e}"
            )
            logger.debug(traceback.format_exc())


def clean_up_node_maps():
    """Stops generating perf maps for each NodeJS process and cleans up generated maps"""
    processes = get_node_processes()
    for process in processes:
        try:
            node_version = get_exe_version(process)
            node_major_version = node_version[1:].split(".")[0]
            pid_inside_ns = _get_pid_inside_ns(process.pid)
            libc = "musl" if is_musl(process) else "glibc"
            dest = os.path.join("/tmp", "node_module", libc, node_major_version)
            cp = run_in_ns(
                ["pid", "mnt", "net"],
                _clean_up_wrapper(dest, pid_inside_ns),
                process.pid,
            )
            if hasattr(cp, "stdout"):
                logger.debug(cp.stdout.decode().strip())
            if hasattr(cp, "stderr"):
                logger.debug(cp.stderr.decode().strip())
            if not is_same_ns(process, NsType.mnt.name):
                os.remove(os.path.join("/tmp", f"perf-{process.pid}.map"))
        except Exception as e:
            logger.warn(
                f"Could not clean up debug symbols for pid {process.pid}. Reason: {e}"
            )
            logger.debug(traceback.format_exc())
