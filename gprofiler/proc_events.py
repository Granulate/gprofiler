import os
import socket
import struct
import threading
from select import select

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


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
_PROC_EVENT_EXIT = 0x80000000

# struct exit_proc_event {
#         __kernel_pid_t process_pid;
#         __kernel_pid_t process_tgid;
#         __u32 exit_code, exit_signal;
# } exit;
_exit_proc_event = struct.Struct("=4I")


def _proc_events_listener():
    """Runs forever and calls registered callbacks"""
    s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, _NETLINK_CONNECTOR)

    try:
        s.bind((os.getpid(), _CN_IDX_PROC))
    except socket.error:
        logger.exception("")

    while True:
        (readable, _, _) = select([s], [], [])
        data = readable[0].recv(256)

        nl_hdr = dict(zip(("len", "type", "flags", "seq", "pid"), _nlmsghdr.unpack(data[: _nlmsghdr.size])))
        if nl_hdr["type"] != _NLMSG_DONE:
            # Handle only netlink messages
            continue

        # Strip off headers
        data = data[_nlmsghdr.size : nl_hdr["len"]]
        data = data[_cn_msg.size :]

        event = dict(zip(("what", "cpu", "timestamp_ns"), _base_proc_event.unpack(data[: _base_proc_event.size])))

        if event["what"] == _PROC_EVENT_EXIT:
            # (exit_signal is the signal that the parent process received on exit)
            event_data = dict(
                zip(
                    ("pid", "tgid", "exit_code", "exit_signal"),
                    _exit_proc_event.unpack(
                        data[_base_proc_event.size : _base_proc_event.size + _exit_proc_event.size]
                    ),
                )
            )

            for callback in _exit_callbacks:
                callback(event_data["pid"], event_data["exit_code"])


_listener_thread = threading.Thread(target=_proc_events_listener, daemon=True)


def _ensure_is_running():
    if not _listener_thread.isAlive():
        try:
            _listener_thread.start()
        except RuntimeError:
            logger.exception("proc_events execution failed")


_exit_callbacks = []


def register_exit_callback(callback):
    """Register a function to be called whenever a process exits

    The callback should receive two arguments: pid and exit_code.
    """
    _ensure_is_running()
    _exit_callbacks.append(callback)
