#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from abc import ABC, abstractmethod
from typing import Iterable, Tuple, Type

from granulate_utils.linux import get_kernel_release

KernelMessage = Tuple[float, int, str]


class KernelMessagesProvider(ABC):
    @abstractmethod
    def iter_new_messages(self) -> Iterable[KernelMessage]:
        pass

    def on_missed(self):
        """Gets called when some kernel messages are missed."""
        pass


class EmptyKernelMessagesProvider(KernelMessagesProvider):
    def iter_new_messages(self):
        return []


DefaultKernelMessagesProvider: Type[KernelMessagesProvider]

if get_kernel_release() >= (3, 5):
    from granulate_utils.linux.devkmsg import DevKmsgProvider

    DefaultKernelMessagesProvider = DevKmsgProvider
else:
    DefaultKernelMessagesProvider = EmptyKernelMessagesProvider
