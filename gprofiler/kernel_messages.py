from gprofiler.log import get_logger_adapter
from gprofiler.utils import get_kernel_release

logger = get_logger_adapter(__name__)
kernel_release = get_kernel_release()


class DummyMessageReader:
    def __init__(self):
        print("This kernel does not support the new /dev/kmsg interface for reading messages.")
        print("Profilee error monitoring not available.")
        print()
        logger.warning("Profilee error monitoring not available.")

    def iter_new_messages(self):
        return []


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


if kernel_release >= (3, 5):
    from gprofiler.devkmsg import DevKmsgReader

    DefaultMessageReader = DevKmsgReader
else:
    DefaultMessageReader = DummyMessageReader
