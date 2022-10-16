#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from granulate_utils.linux.kernel_messages import (
    DefaultKernelMessagesProvider,
    EmptyKernelMessagesProvider,
    KernelMessagesProvider,
)

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


# type ignored because mypy doesn't like Type[...] variables defined conditionally, later used as types
# themselves (which is what we do here).
# see https://mypy.readthedocs.io/en/stable/common_issues.html#variables-vs-type-aliases
class GProfilerKernelMessagesProvider(DefaultKernelMessagesProvider):  # type: ignore
    def on_missed(self) -> None:
        logger.warning("Missed some kernel messages.")


def get_kernel_messages_provider() -> KernelMessagesProvider:
    if DefaultKernelMessagesProvider is EmptyKernelMessagesProvider:
        logger.info(
            "Profilee error monitoring via kernel messages is not supported for this system"
            " (this does not prevent profiling)"
        )
        return DefaultKernelMessagesProvider()

    try:
        return GProfilerKernelMessagesProvider()
    except Exception:
        logger.warning(
            "Failed to start kernel messages listener. Profilee error monitoring via kernel messages"
            " is not available (this does not prevent profiling). Do you have permission"
            " to read /dev/kmsg?",
            exc_info=True,
        )
        return EmptyKernelMessagesProvider()
