import json
import os
import shutil
import signal
import stat
from typing import Dict, List

import psutil
import requests
from granulate_utils.linux.ns import get_proc_root_path, get_process_nspid, resolve_proc_root_links, run_in_ns
from granulate_utils.linux.process import is_musl
from retry import retry
from websocket import create_connection
from websocket._core import WebSocket

from gprofiler.log import get_logger_adapter
from gprofiler.metadata import get_exe_version
from gprofiler.utils import add_permission_dir, pgrep_exe, resource_path

logger = get_logger_adapter(__name__)

DSO_GIT_REVISION = "20eb88a"


class NodeDebuggerUrlNotFound(Exception):
    pass


class NodeDebuggerUnexpectedResponse(Exception):
    pass


def get_dest_inside_container(musl: bool, node_version: str) -> str:
    libc = "musl" if musl else "glibc"
    return os.path.join("/", "tmp", "node_module", DSO_GIT_REVISION, libc, node_version)


def _start_debugger(pid: int) -> None:
    # for windows: in shell node -e "process._debugProcess(PID)"
    os.kill(pid, signal.SIGUSR1)


@retry(NodeDebuggerUrlNotFound, 5, 1)
def _get_debugger_url() -> str:
    # when killing process with SIGUSR1 it will open new debugger session on port 9229,
    # so it will always the same
    port = 9229
    debugger_url_response = requests.get(f"http://127.0.0.1:{port}/json/list")
    if (
        debugger_url_response.status_code != 200
        or not debugger_url_response.headers.get("Content-Type")
        or "application/json" not in debugger_url_response.headers.get("Content-Type")  # type: ignore
    ):
        raise NodeDebuggerUrlNotFound(
            {"status_code": debugger_url_response.status_code, "text": debugger_url_response.text}
        )

    response_json = debugger_url_response.json()
    if (
        not isinstance(response_json, list)
        or len(response_json) == 0
        or not isinstance(response_json[0], dict)
        or "webSocketDebuggerUrl" not in response_json[0]
    ):
        raise NodeDebuggerUrlNotFound(response_json)

    return response_json[0]["webSocketDebuggerUrl"]  # type: ignore


@retry(NodeDebuggerUnexpectedResponse, 5, 1)
def _send_socket_request(sock: WebSocket, cdp_request: Dict) -> None:
    sock.settimeout(2)
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


def _load_dso(sock: WebSocket, module_path: str) -> None:
    cdp_request = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": f'process.mainModule.require("{os.path.join(module_path, "linux-perf.js")}").start()',
            "replMode": True,
        },
    }
    _send_socket_request(sock, cdp_request)


def _stop_dso(sock: WebSocket, module_path: str) -> None:
    cdp_request = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": f'process.mainModule.require("{os.path.join(module_path, "linux-perf.js")}").stop()',
            "replMode": True,
        },
    }
    _send_socket_request(sock, cdp_request)


def _copy_module_into_process_ns(process: psutil.Process, musl: bool, version: str) -> str:
    proc_root = get_proc_root_path(process)
    libc = "musl" if musl else "glibc"
    dest_inside_container = get_dest_inside_container(musl, version)
    dest = resolve_proc_root_links(proc_root, dest_inside_container)
    if os.path.exists(dest):
        return dest_inside_container
    src = resource_path(os.path.join("node", "module", libc, version))
    shutil.copytree(src, dest)
    add_permission_dir(dest, stat.S_IROTH, stat.S_IXOTH | stat.S_IROTH)
    return dest_inside_container


def _generate_perf_map(module_path: str) -> None:
    debugger_url = _get_debugger_url()
    sock = create_connection(debugger_url)
    sock.settimeout(2)
    _load_dso(sock, module_path)


def _clean_up(module_path: str, pid: int) -> None:
    debugger_url = _get_debugger_url()
    sock = create_connection(debugger_url)
    _stop_dso(sock, module_path)
    sock.settimeout(2)
    os.remove(os.path.join("/tmp", f"perf-{pid}.map"))


def get_node_processes() -> List[psutil.Process]:
    return pgrep_exe(r"(?:^.+/node[^/]*$)")


def generate_map_for_node_processes(processes: List[psutil.Process]) -> None:
    """Iterates over all NodeJS processes, starts debugger for it, finds debugger URL,
    copies node-linux-perf module into process' namespace, loads module and starts it.
    After that it links it into gProfiler namespace's /tmp. This lets perf load it"""
    for process in processes:
        try:
            musl = is_musl(process)
            node_version = get_exe_version(process)
            node_major_version = node_version[1:].split(".")[0]
            dest = _copy_module_into_process_ns(process, musl, node_major_version)
            _start_debugger(process.pid)
            run_in_ns(["pid", "mnt", "net"], lambda: _generate_perf_map(dest), process.pid, passthrough_exception=True)
        except Exception as e:
            logger.warning(f"Could not create debug symbols for pid {process.pid}. Reason: {e}", exc_info=True)


def clean_up_node_maps(processes: List[psutil.Process]) -> None:
    """Stops generating perf maps for each NodeJS process and cleans up generated maps"""
    for process in processes:
        try:
            node_version = get_exe_version(process)
            node_major_version = node_version[1:].split(".")[0]
            pid_inside_ns = get_process_nspid(process.pid)
            dest = get_dest_inside_container(is_musl(process), node_major_version)
            run_in_ns(
                ["pid", "mnt", "net"],
                lambda: _clean_up(dest, pid_inside_ns),
                process.pid,
                passthrough_exception=True,
            )
        except Exception as e:
            logger.warning(f"Could not clean up debug symbols for pid {process.pid}. Reason: {e}", exc_info=True)
