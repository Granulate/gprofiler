#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from granulate_utils.linux import get_kernel_release
from granulate_utils.linux.kernel_messages import DefaultKernelMessagesProvider, EmptyKernelMessagesProvider

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


class GProfilerKernelMessagesProvider(DefaultKernelMessagesProvider):
    def on_missed(self):
        logger.warning("Missed some kernel messages.")


def get_kernel_messages_provider():
    if get_kernel_release() < (3, 5):
        print(
            "This kernel does not support the new /dev/kmsg interface for reading messages,"
            " or you lack the permissions for it."
        )
        print("Profilee error monitoring not available.")
        print()
        logger.warning("Profilee error monitoring not available.")

    try:
        return GProfilerKernelMessagesProvider()
    except Exception:
        logger.warning("Failed to start kernel messages listener.", exc_info=True)
        logger.warning("Profilee error monitoring not available.")
        return EmptyKernelMessagesProvider()
