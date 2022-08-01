#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

"""Callbacks for process events

This module uses the Linux's Process Connector mechanism to invoke registered callbacks on various process events.
To achieve this, once the first callback is registered a thread is invoked to listen to these events and execute the
registered callbacks.

This interface with the kernel isn't too well documented, but to learn more about it you can read this blog post:
https://nick-black.com/dankwiki/index.php/The_Proc_Connector_and_Socket_Filters


TODO: Add more callbacks.
TODO: Use socket filter to avoid waking up for irrelevant events.
"""
import os
import selectors
import socket
import struct
import threading
from typing import Callable, List, Optional

from granulate_utils.linux.ns import run_in_ns


def _raise_if_not_running(func: Callable):
    def wrapper(self, *args, **kwargs):
        if not self.is_alive():
            raise RuntimeError("Process Events Listener wasn't started")
        return func(self, *args, **kwargs)

    return wrapper


class _ProcEventsListener(threading.Thread):
    """Thead listening to process events"""

    # linux/netlink.h:
    _NETLINK_CONNECTOR = 11

    # linux/netlink.h:
    _NLMSG_DONE = 0x3  # End of a dump

    # struct nlmsghdr {
    #         __u32           nlmsg_len;      /* Length of message including header */
    #         __u16           nlmsg_type;     /* Message content */
    #         __u16           nlmsg_flags;    /* Additional flags */
    #         __u32           nlmsg_seq;      /* Sequence number */
    #         __u32           nlmsg_pid;      /* Sending process port ID */
    # };
    _nlmsghdr = struct.Struct("=I2H2I")

    # linux/connector.h:
    _CN_IDX_PROC = 0x1
    _CN_VAL_PROC = 0x1

    # struct cn_msg {
    #         struct cb_id id;
    #
    #         __u32 seq;
    #         __u32 ack;
    #
    #         __u16 len;              /* Length of the following data */
    #         __u16 flags;
    #         __u8 data[0];
    # };
    _cn_msg = struct.Struct("=4I2H")

    # linux/cn_proc.h:
    # struct proc_event {
    #         enum what {
    #                 ...
    #         } what;
    #         __u32 cpu;
    #         __u64 __attribute__((aligned(8))) timestamp_ns;
    #                 /* Number of nano seconds since system boot */
    #         union { /* must be last field of proc_event struct */
    #                 ...
    #         } event_data;
    # };
    _base_proc_event = struct.Struct("=2IQ")

    # From enum what
    _PROC_EVENT_EXEC = 0x00000002
    _PROC_EVENT_EXIT = 0x80000000

    # From enum proc_cn_mcast_op
    _PROC_CN_MCAST_LISTEN = 1

    # struct exit_proc_event {
    #         __kernel_pid_t process_pid;
    #         __kernel_pid_t process_tgid;
    #         __u32 exit_code, exit_signal;
    # } exit;
    _exit_proc_event = struct.Struct("=4I")

    # struct exec_proc_event {
    #     pid_t process_pid;
    #     pid_t process_tgid;
    # } exec;
    _exec_proc_event = struct.Struct("=2I")

    def __init__(self):
        self._socket = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, self._NETLINK_CONNECTOR)
        self._exit_callbacks: List[Callable] = []
        self._exec_callbacks: List[Callable] = []
        self._should_stop = False

        self._selector = selectors.DefaultSelector()
        # Create a pipe so we can make select() return
        self._select_breaker_reader, self._select_breaker = os.pipe()
        self._selector.register(self._select_breaker_reader, selectors.EVENT_READ)

        super().__init__(target=self._proc_events_listener, name="Process Events Listener", daemon=True)

    def _register_for_connector_events(self, socket: socket.socket):
        """Notify the kernel that we're listening for events on the connector"""
        cn_proc_op = struct.Struct("=I").pack(self._PROC_CN_MCAST_LISTEN)
        cn_msg = self._cn_msg.pack(self._CN_IDX_PROC, self._CN_VAL_PROC, 0, 0, len(cn_proc_op), 0) + cn_proc_op
        nl_msg = self._nlmsghdr.pack(self._nlmsghdr.size + len(cn_msg), self._NLMSG_DONE, 0, 0, os.getpid()) + cn_msg

        socket.send(nl_msg)

    def _listener_loop(self):
        while not self._should_stop:
            events = self._selector.select()
            if self._should_stop:
                break

            for key, _ in events:
                try:
                    # When stressed, reading from the socket can raise
                    #   OSError: [Errno 105] No buffer space available
                    # This seems to be safe to ignore, empirically no events were missed
                    data = key.fileobj.recv(256)
                except OSError as e:
                    if e.errno == 105:
                        continue
                    raise

                nl_hdr = dict(
                    zip(("len", "type", "flags", "seq", "pid"), self._nlmsghdr.unpack(data[: self._nlmsghdr.size]))
                )
                if nl_hdr["type"] != self._NLMSG_DONE:
                    # Handle only netlink messages
                    continue

                # Strip off headers
                data = data[self._nlmsghdr.size : nl_hdr["len"]]
                data = data[self._cn_msg.size :]

                event = dict(
                    zip(
                        ("what", "cpu", "timestamp_ns"),
                        self._base_proc_event.unpack(data[: self._base_proc_event.size]),
                    )
                )

                if event["what"] == self._PROC_EVENT_EXIT:
                    # (Notice that exit_signal is the signal that the parent process received on exit, and not the
                    # signal that caused it)
                    event_data = dict(
                        zip(
                            ("pid", "tgid", "exit_code", "exit_signal"),
                            self._exit_proc_event.unpack(
                                data[
                                    self._base_proc_event.size : self._base_proc_event.size + self._exit_proc_event.size
                                ]
                            ),
                        )
                    )

                    for callback in self._exit_callbacks:
                        callback(event_data["pid"], event_data["tgid"], event_data["exit_code"])
                elif event["what"] == self._PROC_EVENT_EXEC:
                    event_data = dict(
                        zip(
                            ("pid", "tgid"),
                            self._exec_proc_event.unpack(
                                data[
                                    self._base_proc_event.size : self._base_proc_event.size + self._exec_proc_event.size
                                ]
                            ),
                        )
                    )

                    for callback in self._exec_callbacks:
                        callback(event_data["pid"], event_data["tgid"])

    def _proc_events_listener(self):
        """Runs forever and calls registered callbacks on process events"""
        self._selector.register(self._socket, selectors.EVENT_READ)

        try:
            self._listener_loop()
        finally:
            # Cleanup
            self._selector.unregister(self._socket)
            self._selector.unregister(self._select_breaker_reader)
            self._socket.close()
            os.close(self._select_breaker)
            os.close(self._select_breaker_reader)

    def start(self):
        # We make these initializations here (and not in the new thread) so if an exception occures it'll be
        # visible in the calling thread
        try:
            self._socket.bind((0, self._CN_IDX_PROC))
            self._register_for_connector_events(self._socket)
        except PermissionError as e:
            raise PermissionError(
                "This process doesn't have permissions to bind/connect to the process events connector"
            ) from e

        super().start()

    @_raise_if_not_running
    def stop(self):
        self._should_stop = True
        # Write to make select() return
        os.write(self._select_breaker, b"\0")

    @_raise_if_not_running
    def register_exit_callback(self, callback: Callable):
        self._exit_callbacks.append(callback)

    @_raise_if_not_running
    def unregister_exit_callback(self, callback: Callable):
        self._exit_callbacks.remove(callback)

    @_raise_if_not_running
    def register_exec_callback(self, callback: Callable):
        self._exec_callbacks.append(callback)

    @_raise_if_not_running
    def unregister_exec_callback(self, callback: Callable):
        self._exec_callbacks.remove(callback)


_proc_events_listener: Optional[_ProcEventsListener] = None
_listener_creation_lock = threading.Lock()


def _start_listener():
    listener = _ProcEventsListener()
    listener.start()
    return listener


def _ensure_thread_started(func: Callable):
    def wrapper(*args, **kwargs):
        global _proc_events_listener

        with _listener_creation_lock:
            if _proc_events_listener is None:
                try:
                    # needs to run in init net NS - see netlink_kernel_create() call on init_net in cn_init().
                    _proc_events_listener = run_in_ns(["net"], _start_listener)
                except Exception:
                    # TODO: We leak the pipe FDs here...
                    _proc_events_listener = None
                    raise

        if not _proc_events_listener.is_alive():
            raise RuntimeError("Process Events Listener isn't running")

        return func(*args, **kwargs)

    return wrapper


@_ensure_thread_started
def register_exit_callback(callback: Callable):
    """Register a function to be called whenever a process exits

    The callback should receive three arguments: tid, pid and exit_code.
    """
    assert _proc_events_listener is not None
    _proc_events_listener.register_exit_callback(callback)


@_ensure_thread_started
def unregister_exit_callback(callback: Callable):
    assert _proc_events_listener is not None
    _proc_events_listener.unregister_exit_callback(callback)


@_ensure_thread_started
def register_exec_callback(callback: Callable):
    """Register a function to be called whenever exec is called on a process

    The callback should receive two arguments: pid and tgid.
    """
    assert _proc_events_listener is not None
    _proc_events_listener.register_exec_callback(callback)


@_ensure_thread_started
def unregister_exec_callback(callback: Callable):
    assert _proc_events_listener is not None
    _proc_events_listener.unregister_exec_callback(callback)
