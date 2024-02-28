#
# Copyright (C) 2023 Intel Corporation
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
