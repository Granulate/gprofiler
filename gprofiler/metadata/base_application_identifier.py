#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from abc import ABCMeta, abstractmethod
from typing import Optional

from psutil import Process

from gprofiler.metadata.enrichment import EnrichmentOptions


class _ApplicationIdentifier(metaclass=ABCMeta):
    enrichment_options: Optional[EnrichmentOptions] = None

    @abstractmethod
    def get_app_id(self, process: Process) -> Optional[str]:
        pass
