#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
import os
import time
from datetime import datetime
from threading import Event, Thread
from typing import Optional

from granulate_utils.metrics.sampler import BigDataSampler

from gprofiler.client import APIClient, bake_metrics_payload
from gprofiler.log import get_logger_adapter
from gprofiler.utils import get_iso8601_format_time
from gprofiler.utils.fs import escape_filename

FIND_CLUSTER_TIMEOUT_SECS = 10 * 60

logger = get_logger_adapter(__name__)


class SparkSampler:
    """
    Spark cluster metrics sampler
    """

    def __init__(
        self,
        sample_period: float,
        storage_dir: str,
        api_client: Optional[APIClient] = None,
    ):
        self._master_address: Optional[str] = None
        self._spark_mode: Optional[str] = None
        self._collection_thread: Optional[Thread] = None
        self._sample_period = sample_period
        # not the same instance as GProfiler._stop_event
        self._stop_event = Event()
        self._spark_sampler: Optional[BigDataSampler] = None
        self._stop_collection = False
        self._is_running = False
        self._applications_metrics = False
        self._storage_dir = storage_dir
        if self._storage_dir is not None:
            assert os.path.exists(self._storage_dir) and os.path.isdir(self._storage_dir)
        else:
            logger.debug("Output directory is None. Will add metrics to queue")
        self._client = api_client
        self._spark_sampler = BigDataSampler(logger, self._master_address, self._spark_mode, self._applications_metrics)

    def start(self) -> None:
        self._stop_event.clear()
        self._collection_thread = Thread(target=self._collect_loop)
        self._collection_thread.start()
        self._is_running = True

    def _collect_loop(self) -> None:
        assert (
            self._client is not None or self._storage_dir is not None
        ), "A valid API client or storage directory is required"
        timefn = time.monotonic
        start_time = timefn()
        while not self._stop_event.is_set():
            if self._spark_sampler is not None:
                discovered = self._spark_sampler.discover()
                if discovered is False:
                    if timefn() - start_time >= FIND_CLUSTER_TIMEOUT_SECS:
                        logger.info("Timed out identifying Spark cluster. Stopping Spark collector.")
                        break

                elif discovered is True:
                    snapshot = self._spark_sampler.collect_loop_helper()
                    logger.debug("Collected Spark metrics", snapshot=snapshot)
                    # No need to submit samples that don't actually have a value:
                    if self._storage_dir is not None and snapshot is not None:
                        now = get_iso8601_format_time(datetime.now())
                        base_filename = os.path.join(self._storage_dir, f"spark_metric_{escape_filename(now)}")
                        with open(base_filename, "w") as f:
                            json.dump(bake_metrics_payload(snapshot), f)
                    if self._client is not None:
                        self._client.submit_spark_metrics(snapshot)

                self._stop_event.wait(self._sample_period)

        self._is_running = False

    def stop(self) -> None:
        if self._is_running and self._collection_thread is not None and self._collection_thread.is_alive():
            self._stop_event.set()
            self._collection_thread.join()

    def is_running(self) -> bool:
        return self._is_running
