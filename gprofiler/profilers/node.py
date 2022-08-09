#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
"""
This script allows user to inject node-linux-perf module and start it to generate perf maps at runtime.
To prepare module run npm install --prefix <location> linux-perf or clone it from https://github.com/mmarchini-oss/node-linux-perf and run npm install 
This only works for applications, that have CommonJS as entry script.

It can be also easly modified to utilize any other module by changing expression in CDP request.
"""

import asyncio
import websockets
import json
import requests
import os
import signal
import time
import psutil
import argparse

EXPECTED_CDP_RESPONSE = {
    "id": 1,
    "result": {"result": {"type": "boolean", "value": True}},
}


class NodeDebuggerUrlNotFound(Exception):
    pass


class NodeDebuggerUnexpectedResponse(Exception):
    pass


def retry(exception, retries, waitTime):
    def wrap(f):
        def wrapped(*args):
            for _ in range(0, retries):
                try:
                    return f(*args)
                except exception:
                    time.sleep(waitTime)

        return wrapped

    return wrap


def __start_debugger(pid):
    os.kill(pid, signal.SIGUSR1)


@retry(NodeDebuggerUrlNotFound, 5, 1)
def __get_debugger_url(pid):
    process = psutil.Process(pid)
    possible_ports = [connection.laddr.port for connection in process.connections()]
    debugger_url = ""
    for port in possible_ports:
        possible_port_response = requests.get(f"http://127.0.0.1:{port}/json/list")
        if (
            possible_port_response.status_code != 200
            or not "application/json"
            in possible_port_response.headers.get("Content-Type")
        ):
            continue

        response_json = possible_port_response.json()
        if (
            not isinstance(response_json, list)
            or len(response_json) == 0
            or not isinstance(response_json[0], dict)
            or not "webSocketDebuggerUrl" in response_json[0].keys()
        ):
            continue

        debugger_url = response_json[0]["webSocketDebuggerUrl"]
    if not debugger_url:
        raise NodeDebuggerUrlNotFound
    return debugger_url


@retry(NodeDebuggerUnexpectedResponse, 5, 1)
async def __load_dso(sock, module_path):
    cdp_request = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": f'process.mainModule.require("{module_path}").start()',
            "replMode": True,
        },
    }
    await sock.send(json.dumps(cdp_request))
    message = await sock.recv()
    if json.loads(message) != EXPECTED_CDP_RESPONSE:
        raise NodeDebuggerUnexpectedResponse(json.loads(message))


async def generate_perf_map(pid, module_path):
    __start_debugger(pid)
    debugger_url = __get_debugger_url(pid)
    async with websockets.connect(debugger_url) as sock:
        await __load_dso(sock, module_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-p",
        "--pid",
        type=int,
        help="PID of nodejs process to which DSO should be injected",
    )
    parser.add_argument(
        "-m",
        "--module_path",
        type=str,
        help="Path to compiled node-linux-perf module. Module must be compiled for specific nodejs version",
    )
    args = parser.parse_args()
    asyncio.run(generate_perf_map(args.pid, args.module_path))
