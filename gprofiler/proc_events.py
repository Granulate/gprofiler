import os
import selectors
import socket
import struct
import threading

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


def _raise_if_not_running(func):
    def wrapper(self, *args, **kwargs):
        if not self.is_alive():
            raise RuntimeError("Process Events Listener wasn't started")
        return func(self, *args, **kwargs)

    return wrapper


class ProcEventsListener(threading.Thread):
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
    _PROC_EVENT_EXIT = 0x80000000

    # From enum proc_cn_mcast_op
    _PROC_CN_MCAST_LISTEN = 1

    # struct exit_proc_event {
    #         __kernel_pid_t process_pid;
    #         __kernel_pid_t process_tgid;
    #         __u32 exit_code, exit_signal;
    # } exit;
    _exit_proc_event = struct.Struct("=4I")

    def __init__(self):
        self._socket = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, self._NETLINK_CONNECTOR)
        self._exit_callbacks = []
        self._should_stop = False

        super().__init__(target=self._proc_events_listener, name="Process Events Listener", daemon=True)

    def _register_for_connector_events(self, socket):
        """Notify the kernel that we're listening for events on the connector"""
        cn_proc_op = struct.Struct("=I").pack(self._PROC_CN_MCAST_LISTEN)
        cn_msg = self._cn_msg.pack(self._CN_IDX_PROC, self._CN_VAL_PROC, 0, 0, len(cn_proc_op), 0) + cn_proc_op
        nl_msg = self._nlmsghdr.pack(self._nlmsghdr.size + len(cn_msg), self._NLMSG_DONE, 0, 0, os.getpid()) + cn_msg

        socket.send(nl_msg)

    def _proc_events_listener(self):
        """Runs forever and calls registered callbacks on process events"""
        try:
            self._socket.bind((os.getpid(), self._CN_IDX_PROC))
        except socket.error:
            logger.exception("")

        self._register_for_connector_events(self._socket)
        selector = selectors.DefaultSelector()
        selector.register(self._socket, selectors.EVENT_READ)

        while not self._should_stop:
            events = selector.select()
            if self._should_stop:
                break

            for key, _ in events:
                data = key.fileobj.recv(256)

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
                    # (exit_signal is the signal that the parent process received on exit)
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
                        callback(event_data["pid"], event_data["exit_code"])

    @_raise_if_not_running
    def stop(self):
        self._should_stop = True
        self._socket.close()

    @_raise_if_not_running
    def register_exit_callback(self, callback):
        """Register a function to be called whenever a process exits

        The callback should receive two arguments: pid and exit_code.
        """
        self._exit_callbacks.append(callback)
