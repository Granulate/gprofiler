#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import errno
import socket
from typing import Dict

from granulate_utils.exceptions import CouldNotAcquireMutex

# Keep the sockets open by holding a reference to them in this dict
_mutexes: Dict[str, socket.socket] = {}


def try_acquire_mutex(name: str) -> None:
    """
    Try to acquire a system-wide mutex named `name`. If it is already acquired an exception is raised.

    The mutex is implemented using a Unix domain socket bound to an abstract address. This provides automatic cleanup
    when the process goes down, and does not make any assumptions about filesystem structure (as happens with file-based
    locks). See unix(7) for more info.
    To see who's holding the lock now, you can run "sudo netstat -xp | grep <name>".
    """

    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind("\0" + name)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        raise CouldNotAcquireMutex(name) from None
    else:
        _mutexes[name] = sock


def release_mutex(name: str) -> None:
    """Release a currently held mutex named `name`."""
    try:
        sock = _mutexes.pop(name)
    except KeyError:
        raise Exception(f"Mutex {name!r} was not acquired!") from None
    else:
        sock.close()
