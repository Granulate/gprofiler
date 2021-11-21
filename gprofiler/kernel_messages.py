import os
import selectors
import time

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


class DevKmsgReader:
    def __init__(self):
        self.dev_kmsg = open("/dev/kmsg")
        # skip all historical messages:
        os.lseek(self.dev_kmsg.fileno(), 0, os.SEEK_END)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.dev_kmsg, selectors.EVENT_READ)

    def iter_new_messages(self):
        messages = []
        while self.selector.select(0):
            line = self.dev_kmsg.readline()[:-1]
            if line[0] == " ":
                assert len(messages) > 0
                message = messages[-1]
                messages[-1] = (message[0], message[1] + "\n" + line[1:])
            else:
                messages.append((time.time(), line))
        yield from self._parse_raw_messages(messages)

    def _parse_raw_messages(self, messages):
        for timestamp, message in messages:
            prefix, text = message.split(";", maxsplit=1)
            fields = prefix.split(",")
            level = int(fields[0])
            yield timestamp, level, text


class KernelMessagePublisher:
    def __init__(self, reader):
        self.reader = reader
        self.subscribers = []

    def handle_new_messages(self):
        for message in self.reader.iter_new_messages():
            for subscriber in self.subscribers:
                try:
                    subscriber(message[2])
                except Exception:
                    logger.exception(f"Error handling message: {message[2]}")

    def subscribe(self, callback):
        self.subscribers.append(callback)
