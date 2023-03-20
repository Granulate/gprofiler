#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
import os
import time
from datetime import datetime, timezone
from threading import Event, Thread
from typing import Optional, Tuple
from xml.etree import ElementTree as ET

import psutil
from granulate_utils.exceptions import MissingExePath
from granulate_utils.linux.ns import resolve_host_path
from granulate_utils.linux.process import process_exe

from gprofiler.client import APIClient, bake_metrics_payload
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.system_metadata import get_hostname
from gprofiler.metrics import MetricsSnapshot
from gprofiler.spark.collector import SparkCollector
from gprofiler.spark.mode import SPARK_MESOS_MODE, SPARK_STANDALONE_MODE, SPARK_YARN_MODE
from gprofiler.utils import get_iso8601_format_time
from gprofiler.utils.fs import escape_filename
from gprofiler.utils.process import search_for_process

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
        self._spark_sampler: Optional[SparkCollector] = None
        self._stop_collection = False
        self._is_running = False
        self._storage_dir = storage_dir
        if self._storage_dir is not None:
            assert os.path.exists(self._storage_dir) and os.path.isdir(self._storage_dir)
        else:
            logger.debug("Output directory is None. Will add metrics to queue")
        self._client = api_client

    def _get_yarn_config_path(self, process: psutil.Process) -> str:
        env = process.environ()
        if "HADOOP_CONF_DIR" in env:
            path = env["HADOOP_CONF_DIR"]
            logger.debug("Found HADOOP_CONF_DIR variable", hadoop_conf_dir=path)
        else:
            path = "/etc/hadoop/conf/"
            logger.info("Could not find HADOOP_CONF_DIR variable, using default path", hadoop_conf_dir=path)
        return os.path.join(path, "yarn-site.xml")

    def _get_yarn_config(self, process: psutil.Process) -> Optional[ET.Element]:
        config_path = self._get_yarn_config_path(process)

        logger.debug("Trying to open yarn config file for reading", config_path=config_path)
        try:
            # resolve config path against process' filesystem root
            process_relative_config_path = resolve_host_path(process, self._get_yarn_config_path(process))
            with open(process_relative_config_path, "rb") as conf_file:
                config_xml_string = conf_file.read()
            return ET.fromstring(config_xml_string)
        except FileNotFoundError:
            return None

    def _get_yarn_config_property(
        self, process: psutil.Process, requested_property: str, default: Optional[str] = None
    ) -> Optional[str]:
        config = self._get_yarn_config(process)
        if config is not None:
            for config_property in config.iter("property"):
                name_property = config_property.find("name")
                if name_property is not None and name_property.text == requested_property:
                    value_property = config_property.find("value")
                    if value_property is not None:
                        return value_property.text
        return default

    def _guess_standalone_master_webapp_address(self, process: psutil.Process) -> str:
        """
        Selects the master address for a standalone cluster.
        Uses master_address if given.
        """
        if self._master_address:
            return self._master_address
        else:
            master_process_args = process.cmdline()
            master_ip = master_process_args[master_process_args.index("--host") + 1]
            master_port = master_process_args[master_process_args.index("--webui-port") + 1]
            return f"{master_ip}:{master_port}"

    def _guess_yarn_resource_manager_webapp_address(self, resource_manager_process: psutil.Process) -> str:
        config = self._get_yarn_config(resource_manager_process)

        if config is not None:
            for config_property in config.iter("property"):
                name_property = config_property.find("name")
                if (
                    name_property is not None
                    and name_property.text is not None
                    and name_property.text.startswith("yarn.resourcemanager.webapp.address")
                ):
                    value_property = config_property.find("value")
                    if value_property is not None and value_property.text is not None:
                        return value_property.text

        if self._master_address is not None:
            return self._master_address
        else:
            host_name = self._get_yarn_host_name(resource_manager_process)
            return host_name + ":8088"

    def _guess_mesos_master_webapp_address(self, process: psutil.Process) -> str:
        """
        Selects the master address for a mesos-master running on this node. Uses master_address if given, or defaults
        to my hostname.
        """
        if self._master_address:
            return self._master_address
        else:
            host_name = get_hostname()
            return host_name + ":5050"

    def _get_yarn_host_name(self, resource_manager_process: psutil.Process) -> str:
        """
        Selects the master adderss for a ResourceManager running on this node - this parses the YARN config to
        get the hostname, and if not found, defaults to my hostname.
        """
        hostname = self._get_yarn_config_property(resource_manager_process, "yarn.resourcemanager.hostname")
        if hostname is not None:
            logger.debug(
                "Selected hostname from yarn.resourcemanager.hostname config", resourcemanager_hostname=hostname
            )
        else:
            hostname = get_hostname()
            logger.debug("Selected hostname from my hostname", resourcemanager_hostname=hostname)
        return hostname

    def _is_yarn_master_collector(self, resource_manager_process: psutil.Process) -> bool:
        """
        yarn lists the addresses of the other masters in order communicate with
        other masters, so we can choose one of them (like rm1) and run the
        collection only on it so we won't get the same metrics for the cluster
        multiple times the rm1 hostname is in both EMR and Azure using the internal
        DNS and it's starts with the host name.

        For example, in AWS EMR:
        rm1 = 'ip-10-79-63-183.us-east-2.compute.internal:8025'
        where the hostname is 'ip-10-79-63-183'.

        In Azure:
        'rm1 = hn0-nrt-hb.3e3rqto3nr5evmsjbqz0pkrj4g.tx.internal.cloudapp.net:8050'
        where the hostname is 'hn0-nrt-hb.3e3rqto3nr5evmsjbqz0pkrj4g'
        """
        rm1_address = self._get_yarn_config_property(resource_manager_process, "yarn.resourcemanager.address.rm1", None)
        host_name = self._get_yarn_host_name(resource_manager_process)

        if rm1_address is None:
            logger.info(
                "yarn.resourcemanager.address.rm1 is not defined in config, so it's a single master deployment,"
                " enabling Spark collector"
            )
            return True
        elif rm1_address.startswith(host_name):
            logger.info(
                f"This is the collector master, because rm1: {rm1_address!r}"
                f" starts with my host name: {host_name!r}, enabling Spark collector"
            )
            return True
        else:
            logger.info(
                f"This is not the collector master, because rm1: {rm1_address!r}"
                f" does not start with my host name: {host_name!r}, skipping Spark collector on this YARN master"
            )
            return False

    def _get_spark_manager_process(self) -> Optional[psutil.Process]:
        def is_master_process(process: psutil.Process) -> bool:
            try:
                return (
                    "org.apache.hadoop.yarn.server.resourcemanager.ResourceManager" in process.cmdline()
                    or "org.apache.spark.deploy.master.Master" in process.cmdline()
                    or "mesos-master" in process_exe(process)
                )
            except MissingExePath:
                return False

        try:
            return next(search_for_process(is_master_process))
        except StopIteration:
            return None

    def _find_spark_cluster(self) -> Optional[Tuple[str, str]]:
        """:return: (master address, cluster mode)"""
        spark_master_process = self._get_spark_manager_process()
        spark_cluster_mode = "unknown"
        webapp_url = None

        if spark_master_process is None:
            logger.debug("Could not find any spark master process (resource manager or spark master)")
            return None

        if "org.apache.hadoop.yarn.server.resourcemanager.ResourceManager" in spark_master_process.cmdline():
            if not self._is_yarn_master_collector(spark_master_process):
                return None
            spark_cluster_mode = SPARK_YARN_MODE
            webapp_url = self._guess_yarn_resource_manager_webapp_address(spark_master_process)
        elif "org.apache.spark.deploy.master.Master" in spark_master_process.cmdline():
            spark_cluster_mode = SPARK_STANDALONE_MODE
            webapp_url = self._guess_standalone_master_webapp_address(spark_master_process)
        elif "mesos-master" in process_exe(spark_master_process):
            spark_cluster_mode = SPARK_MESOS_MODE
            webapp_url = self._guess_mesos_master_webapp_address(spark_master_process)

        if spark_master_process is None or webapp_url is None or spark_cluster_mode == "unknown":
            logger.warning("Could not get proper Spark cluster configuration")
            return None

        logger.info("Guessed settings are", cluster_mode=spark_cluster_mode, webapp_url=webapp_url)

        return webapp_url, spark_cluster_mode

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
            if self._spark_sampler is None:
                spark_cluster_conf = self._find_spark_cluster()
                if spark_cluster_conf is not None:
                    master_address, cluster_mode = spark_cluster_conf
                    self._spark_sampler = SparkCollector(cluster_mode, master_address)
                else:
                    if timefn() - start_time >= FIND_CLUSTER_TIMEOUT_SECS:
                        logger.info("Timed out identifying Spark cluster. Stopping Spark collector.")
                        break

            if self._spark_sampler is not None:
                collected = self._spark_sampler.collect()
                # No need to submit samples that don't actually have a value:
                samples = tuple(filter(lambda s: s.value is not None, collected))
                snapshot = MetricsSnapshot(datetime.now(tz=timezone.utc), samples)
                if self._storage_dir is not None:
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
