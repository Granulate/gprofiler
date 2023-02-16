#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
# (C) Datadog, Inc. 2018-present. All rights reserved.
# Licensed under a 3-clause BSD style license (see LICENSE.bsd3).
#
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from threading import Event, Thread
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from urllib.parse import urljoin, urlparse

import psutil
import requests
from granulate_utils.exceptions import MissingExePath
from granulate_utils.linux.ns import resolve_host_path
from granulate_utils.linux.process import process_exe

from gprofiler.client import APIClient
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.system_metadata import get_hostname
from gprofiler.spark.metrics import (
    SPARK_APPLICATION_DIFF_METRICS,
    SPARK_APPLICATION_GAUGE_METRICS,
    SPARK_EXECUTORS_METRICS,
    SPARK_RDD_METRICS,
    SPARK_STAGE_METRICS,
    SPARK_STREAMING_BATCHES_METRICS,
    SPARK_STREAMING_STATISTICS_METRICS,
    SPARK_STRUCTURED_STREAMING_METRICS,
    SPARK_TASK_SUMMARY_METRICS,
    YARN_CLUSTER_METRICS,
    YARN_NODES_METRICS,
)
from gprofiler.utils import get_iso8601_format_time
from gprofiler.utils.fs import escape_filename
from gprofiler.utils.process import search_for_process

# Application type and states to collect
YARN_SPARK_APPLICATION_SPECIFIER = "SPARK"
YARN_RUNNING_APPLICATION_SPECIFIER = "RUNNING"

# SPARK modes
SPARK_DRIVER_MODE = "driver"
SPARK_YARN_MODE = "yarn"
SPARK_MESOS_MODE = "mesos"

# COMMON urls
YARN_APPS_PATH = "ws/v1/cluster/apps"
YARN_CLUSTER_PATH = "ws/v1/cluster/metrics"
YARN_NODES_PATH = "ws/v1/cluster/nodes"
SPARK_APPS_PATH = "api/v1/applications"
MESOS_MASTER_APP_PATH = "/frameworks"

STRUCTURED_STREAMS_METRICS_REGEX = re.compile(
    r"^[\w-]+\.driver\.spark\.streaming\.(?P<query_name>[\w-]+)\.(?P<metric_name>[\w-]+)$"
)

logger = get_logger_adapter(__name__)


class SparkCollector:
    def __init__(
        self,
        cluster_mode: str,
        master_address: str,
        *,
        cluster_metrics: bool = True,
        applications_metrics: bool = False,
        streaming_metrics: bool = False,
    ) -> None:
        self._last_sample_time_ms = 0
        self._cluster_mode = cluster_mode
        self._master_address = f"http://{master_address}"
        self._cluster_metrics = cluster_metrics
        self._applications_metrics = applications_metrics
        self._streaming_metrics = streaming_metrics
        self._task_summary_metrics = True
        self._last_iteration_app_job_metrics: Dict[str, Dict[str, Any]] = {}

    def collect(self) -> Generator[Dict[str, Any], None, None]:
        try:
            collected_metrics: Dict[str, Dict[str, Any]] = {}

            if self._cluster_metrics:
                if self._cluster_mode == SPARK_YARN_MODE:
                    self._yarn_cluster_metrics(collected_metrics)
                    self._yarn_nodes_metrics(collected_metrics)
                elif self._cluster_mode == SPARK_DRIVER_MODE:
                    pass

            if self._applications_metrics:
                spark_apps = self._get_running_apps()
                self._spark_application_metrics(collected_metrics, spark_apps)
                self._spark_stage_metrics(collected_metrics, spark_apps)
                self._spark_executor_metrics(collected_metrics, spark_apps)
                if self._streaming_metrics:
                    self._spark_batches_streams_metrics(collected_metrics, spark_apps)
                    self._spark_streaming_statistics_metrics(collected_metrics, spark_apps)
                    self._spark_structured_streams_metrics(collected_metrics, spark_apps)

                # Get the rdd metrics
                self._spark_rdd_metrics(collected_metrics, spark_apps)

            for metric in collected_metrics.values():
                yield metric

            logger.debug("Succeeded gathering spark metrics")
        except Exception:
            logger.exception("Error while trying collect spark metrics")
        finally:
            self._last_sample_time_ms = int(time.monotonic() * 1000)  # need to be in ms

    def _yarn_cluster_metrics(self, collected_metrics: Dict[str, Dict[str, Any]]) -> None:
        try:
            metrics_json = self._rest_request_to_json(self._master_address, YARN_CLUSTER_PATH)

            if metrics_json.get("clusterMetrics") is not None:
                self._set_metrics_from_json(collected_metrics, {}, metrics_json["clusterMetrics"], YARN_CLUSTER_METRICS)
        except Exception:
            logger.exception("Could not gather yarn cluster metrics")

    def _yarn_nodes_metrics(self, collected_metrics: Dict[str, Dict[str, Any]]) -> None:
        try:
            metrics_json = self._rest_request_to_json(self._master_address, YARN_NODES_PATH, states="RUNNING")
            running_nodes = metrics_json.get("nodes", {}).get("node", {})
            for node in running_nodes:
                for metric, value in node.get("resourceUtilization", {}).items():
                    node[metric] = value  # this will create all relevant metrics under same dictionary

                labels = {"node_hostname": node["nodeHostName"]}
                self._set_metrics_from_json(collected_metrics, labels, node, YARN_NODES_METRICS)
        except Exception:
            logger.exception("Could not gather yarn nodes metrics")

    def _spark_application_metrics(
        self, collected_metrics: Dict[str, Dict[str, Any]], running_apps: Dict[str, Tuple[str, str]]
    ) -> None:
        """
        Get metrics for each Spark job.
        """
        iteration_metrics: Dict[str, Dict[str, Any]] = {}
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "jobs")
                application_diff_aggregated_metrics = dict.fromkeys(SPARK_APPLICATION_DIFF_METRICS.keys(), 0)
                application_gauge_aggregated_metrics = dict.fromkeys(SPARK_APPLICATION_GAUGE_METRICS.keys(), 0)
                iteration_metrics[app_id] = {}
                for job in response:
                    iteration_metrics[app_id][job["jobId"]] = job
                    first_time_seen_job = job["jobId"] not in self._last_iteration_app_job_metrics.get(app_id, {})
                    # In order to keep track of an application's metrics, we want to accumulate the values across all
                    # jobs. If the values are numActiveTasks or numActiveStages - there's no problem as they're
                    # always up-to-date and can just be summed. In case of completed jobs, only the last 1000
                    # (configurable - spark.ui.retainedJobs) jobs will be saved and only their metrics will be sent to
                    # us. Older jobs will be deleted , hence we can get into a situation when an old job is deleted,
                    # and then the accumulated metric will get lower, and the diff will be negative. In order to solve
                    # that, we only accumulate the value of newly seen jobs or the diff from the last time the value
                    # was seen.
                    for metric in SPARK_APPLICATION_DIFF_METRICS.keys():
                        if first_time_seen_job:
                            application_diff_aggregated_metrics[metric] += int(job[metric])
                        else:
                            application_diff_aggregated_metrics[metric] += int(job[metric]) - int(
                                self._last_iteration_app_job_metrics[app_id][job["jobId"]][metric]
                            )

                    for metric in SPARK_APPLICATION_GAUGE_METRICS.keys():
                        application_gauge_aggregated_metrics[metric] += int(job[metric])

                labels = {"app_name": app_name, "app_id": app_id}
                self._set_metrics_from_json(
                    collected_metrics, labels, application_diff_aggregated_metrics, SPARK_APPLICATION_DIFF_METRICS
                )
                self._set_metrics_from_json(
                    collected_metrics, labels, application_gauge_aggregated_metrics, SPARK_APPLICATION_GAUGE_METRICS
                )

            except Exception:
                logger.exception("Could not gather spark jobs metrics")
        self._last_iteration_app_job_metrics = iteration_metrics

    def _spark_stage_metrics(
        self, collected_metrics: Dict[str, Dict[str, Any]], running_apps: Dict[str, Tuple[str, str]]
    ) -> None:
        """
        Get metrics for each Spark stage.
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "stages")

                for stage in response:
                    status = stage.get("status")
                    stage_id = stage.get("stageId")
                    labels = {
                        "app_name": app_name,
                        "app_id": app_id,
                        "status": status.lower(),
                        "stage_id": stage_id,
                    }

                    self._set_metrics_from_json(collected_metrics, labels, stage, SPARK_STAGE_METRICS)

                    if self._task_summary_metrics and status == "ACTIVE":
                        stage_response = self._rest_request_to_json(
                            base_url, SPARK_APPS_PATH, app_id, "stages", str(stage_id), details="false", status="ACTIVE"
                        )

                        for attempt in stage_response:
                            try:
                                tasks_summary_response = self._rest_request_to_json(
                                    base_url,
                                    SPARK_APPS_PATH,
                                    app_id,
                                    "stages",
                                    str(stage_id),
                                    str(attempt.get("attemptId")),
                                    "taskSummary",
                                    quantiles="0.5,0.75,0.99",
                                )

                                self._set_task_summary_from_json(
                                    collected_metrics, labels, tasks_summary_response, SPARK_TASK_SUMMARY_METRICS
                                )
                            except Exception:
                                logger.exception("Could not gather spark tasks summary for stage. Skipped.")
            except Exception:
                logger.exception("Could not gather spark stages metrics")

    def _spark_executor_metrics(
        self, collected_metrics: Dict[str, Dict[str, Any]], running_apps: Dict[str, Tuple[str, str]]
    ) -> None:
        """
        Get metrics for each Spark executor.
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                executors = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "executors")
                labels = {"app_name": app_name, "app_id": app_id}
                self._set_metrics_from_json(
                    collected_metrics,
                    labels,
                    {
                        "count": len(executors),
                        "activeCount": len([executor for executor in executors if executor["activeTasks"] > 0]),
                    },
                    SPARK_EXECUTORS_METRICS,
                )
            except Exception:
                logger.exception("Could not gather spark executors metrics")

    def _spark_rdd_metrics(
        self, collected_metrics: Dict[str, Dict[str, Any]], running_apps: Dict[str, Tuple[str, str]]
    ) -> None:
        """
        Get metrics for each Spark RDD.
        """

        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "storage/rdd")

                labels = {"app_name": app_name, "app_id": app_id}

                for rdd in response:
                    self._set_metrics_from_json(collected_metrics, labels, rdd, SPARK_RDD_METRICS)
            except Exception:
                logger.exception("Could not gather Spark RDD metrics")

    def _spark_streaming_statistics_metrics(
        self, collected_metrics: Dict[str, Dict[str, Any]], running_apps: Dict[str, Tuple[str, str]]
    ) -> None:
        """
        Get metrics for each application streaming statistics.
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "streaming/statistics")

                labels = {"app_name": app_name, "app_id": app_id}

                # NOTE: response is a dict
                self._set_metrics_from_json(collected_metrics, labels, response, SPARK_STREAMING_STATISTICS_METRICS)
            except Exception:
                logger.exception("Could not gather Spark streaming metrics")

    @staticmethod
    def _get_last_batches_metrics(
        batches: List[Dict[str, Union[str, int]]], completed_batches: List[Dict[str, Union[str, int]]], n: int
    ) -> Dict[str, float]:
        last = batches[:n]
        last_completed = completed_batches[:n]
        return {
            f"avg{n}_inputSize": sum([int(batch.get("inputSize", 0)) for batch in last]) / len(last),
            f"max{n}_inputSize": max([int(batch.get("inputSize", 0)) for batch in last]),
            f"avg{n}_processingTime": sum([int(batch.get("processingTime", 0)) for batch in last_completed])
            / len(last_completed),
            f"max{n}_processingTime": max([int(batch.get("processingTime", 0)) for batch in last_completed]),
            f"avg{n}_totalDelay": sum([int(batch.get("totalDelay", 0)) for batch in last_completed])
            / len(last_completed),
            f"max{n}_totalDelay": max([int(batch.get("totalDelay", 0)) for batch in last_completed]),
            f"avg{n}_batchDuration": sum([int(batch.get("batchDuration", 0)) for batch in last]) / len(last),
        }

    def _spark_batches_streams_metrics(
        self, collected_metrics: Dict[str, Dict[str, Any]], running_apps: Dict[str, Tuple[str, str]]
    ) -> None:
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                batches = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "/streaming/batches")
                completed_batches = list(
                    filter(
                        lambda batch: batch.get("batchId") is not None and batch.get("status") == "COMPLETED", batches
                    )
                )
                if batches:
                    labels = {
                        "app_name": app_name,
                        "app_id": app_id,
                        "batch_duration": batches[0].get("batchDuration"),
                    }
                    batch_metrics = {
                        "last_inputSize": batches[0].get("inputSize"),
                        "last_processingTime": completed_batches[0].get("processingTime"),
                        "last_totalDelay": completed_batches[0].get("totalDelay"),
                        "last_batchDuration": completed_batches[0].get("batchDuration"),
                    }
                    batch_metrics.update(self._get_last_batches_metrics(batches, completed_batches, 3))
                    batch_metrics.update(self._get_last_batches_metrics(batches, completed_batches, 10))
                    batch_metrics.update(self._get_last_batches_metrics(batches, completed_batches, 25))
                    self._set_metrics_from_json(
                        collected_metrics, labels, batch_metrics, SPARK_STREAMING_BATCHES_METRICS
                    )

            except Exception:
                logger.exception("Could not gather Spark batch metrics for application")

    def _spark_structured_streams_metrics(
        self, collected_metrics: Dict[str, Dict[str, Any]], running_apps: Dict[str, Tuple[str, str]]
    ) -> None:
        """
        Get metrics for each application structured stream.
        Requires:
        - The Metric Servlet to be enabled to path <APP_URL>/metrics/json (enabled by default)
        - `SET spark.sql.streaming.metricsEnabled=true` in the app
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, "/metrics/json")

                response = {
                    metric_name: v["value"]
                    for metric_name, v in response.get("gauges", {}).items()
                    if "streaming" in metric_name and "value" in v
                }
                for gauge_name, value in response.items():
                    match = STRUCTURED_STREAMS_METRICS_REGEX.match(gauge_name)
                    if not match:
                        continue
                    groups = match.groupdict()
                    metric_name = groups["metric_name"]
                    if metric_name not in SPARK_STRUCTURED_STREAMING_METRICS:
                        logger.debug("Unknown metric_name encountered: '%s'", str(metric_name))
                        continue
                    self._set_individual_metric(
                        collected_metrics,
                        SPARK_STRUCTURED_STREAMING_METRICS[metric_name],
                        value,
                        {"app_name": app_name, "app_id": app_id},
                    )
            except Exception:
                logger.exception("Could not gather structured streaming metrics for application")

    def _set_task_summary_from_json(
        self,
        collected_metrics: Dict[str, Dict[str, Any]],
        labels: Dict[str, str],
        metrics_json: Dict[str, List[int]],
        metrics: Dict[str, str],
    ) -> None:
        quantile_index = 0
        if metrics_json is None:
            return
        quantiles_list = metrics_json.get("quantiles")
        if not quantiles_list:
            return

        for quantile in quantiles_list:
            for status, metric in metrics.items():
                metric_status = metrics_json.get(status)
                if not metric_status:
                    continue
                if metric_status[quantile_index] is not None:
                    self._set_individual_metric(
                        collected_metrics, metric, metric_status[quantile_index], {**labels, "quantile": str(quantile)}
                    )
            quantile_index += 1

    def _set_individual_metric(
        self, collected_metrics: Dict[str, Dict[str, Any]], name: str, value: Any, labels: Dict[str, str]
    ) -> None:
        if name not in collected_metrics and value is not None:
            collected_metrics[name] = {
                "name": name,
                "value": value,
                "labels": labels,
            }

    def _set_metrics_from_json(
        self,
        collected_metrics: Dict[str, Dict[str, Any]],
        labels: Dict[str, str],
        metrics_json: Dict[Any, Any],
        metrics: Dict[str, str],
    ) -> None:
        """
        Parse the JSON response and set the metrics
        """
        if metrics_json is None:
            return

        for field_name, metric_name in metrics.items():
            metric_value = metrics_json.get(field_name)
            self._set_individual_metric(collected_metrics, metric_name, metric_value, labels)

    def _get_running_apps(self) -> Dict[str, Tuple[str, str]]:
        """
        Determine what mode was specified
        """
        if self._cluster_mode == SPARK_YARN_MODE:
            running_apps = self._yarn_init()
            return self._get_spark_app_ids(running_apps)
        elif self._cluster_mode == SPARK_DRIVER_MODE:
            return self._driver_init()
        elif self._cluster_mode == SPARK_MESOS_MODE:
            return self._mesos_init()
        else:
            raise ValueError(f"Invalid cluster mode {self._cluster_mode!r}")

    def _driver_init(self) -> Dict[str, Tuple[str, str]]:
        """
        Return a dictionary of {app_id: (app_name, tracking_url)} for the running Spark applications
        """
        return self._driver_get_apps(status=YARN_RUNNING_APPLICATION_SPECIFIER)

    def _driver_get_apps(self, *args: Any, **kwargs: Any) -> Dict[str, Tuple[str, str]]:
        """
        Return a dictionary of {app_id: (app_name, tracking_url)} for the Spark applications
        """
        app_list = {}
        metrics_json = self._rest_request_to_json(self._master_address, SPARK_APPS_PATH, *args, **kwargs)

        for app_json in metrics_json:
            app_id = str(app_json.get("id"))
            app_name = str(app_json.get("name"))
            app_list[app_id] = (app_name, self._master_address)

        return app_list

    def _yarn_init(self) -> Dict[str, Tuple[str, str]]:
        """
        Return a dictionary of {app_id: (app_name, tracking_url)} for running Spark applications.
        """
        return self._yarn_get_spark_apps(
            states=YARN_RUNNING_APPLICATION_SPECIFIER, applicationTypes=YARN_SPARK_APPLICATION_SPECIFIER
        )

    def _yarn_get_spark_apps(self, *args: Any, **kwargs: Any) -> Dict[str, Tuple[str, str]]:
        metrics_json = self._rest_request_to_json(self._master_address, YARN_APPS_PATH, *args, **kwargs)

        running_apps = {}

        if metrics_json.get("apps"):
            if metrics_json["apps"].get("app") is not None:

                for app_json in metrics_json["apps"]["app"]:
                    app_id = app_json.get("id")
                    tracking_url = app_json.get("trackingUrl")
                    app_name = app_json.get("name")

                    if app_id and tracking_url and app_name:
                        running_apps[app_id] = (app_name, tracking_url)

        return running_apps

    def _get_spark_app_ids(self, running_apps: Dict[str, Tuple[str, str]]) -> Dict[str, Tuple[str, str]]:
        """
        Traverses the Spark application master in YARN to get a Spark application ID.
        Return a dictionary of {app_id: (app_name, tracking_url)} for Spark applications
        """
        spark_apps = {}
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                response = self._rest_request_to_json(tracking_url, SPARK_APPS_PATH)

                for app in response:
                    app_id = app.get("id")
                    app_name = app.get("name")

                    if app_id and app_name:
                        spark_apps[app_id] = (app_name, tracking_url)
            except Exception:
                logger.exception("Could not fetch data from url", url=tracking_url)

        return spark_apps

    def _mesos_init(self) -> Dict[str, Tuple[str, str]]:
        running_apps = {}
        metrics_json = self._rest_request_to_json(self._master_address, MESOS_MASTER_APP_PATH)
        for app_json in metrics_json.get("frameworks", []):
            app_id = app_json.get("id")
            tracking_url = app_json.get("webui_url")
            app_name = app_json.get("name")
            if app_id and tracking_url and app_name:
                running_apps[app_id] = (app_name, tracking_url)
        return running_apps

    def _rest_request(self, url: str, object_path: str, *args: Any, **kwargs: Any) -> requests.Response:
        """
        Query the given URL and return the response
        """
        if object_path:
            url = self._join_url_dir(url, object_path)

        # Add args to the url
        if args:
            for directory in args:
                url = self._join_url_dir(url, directory)

        logger.debug(f"Spark check URL: {url}")
        response = requests.get(url, params={k: v for k, v in kwargs.items() if v is not None}, timeout=3)
        response.raise_for_status()
        return response

    def _rest_request_to_json(self, address: str, object_path: str, *args: Any, **kwargs: Any) -> Any:
        """
        Query the given URL and return the JSON response
        """
        return self._rest_request(address, object_path, *args, **kwargs).json()

    def _get_request_url(self, url: str) -> str:
        """
        Get the request address, build with proxy if necessary
        """
        parsed = urlparse(url)

        _url = url
        if not (parsed.netloc and parsed.scheme):
            _url = urljoin(self._master_address, parsed.path)

        return _url

    @staticmethod
    def _join_url_dir(url: str, *args: Any) -> str:
        """
        Join a URL with multiple directories
        """
        for path in args:
            url = url.rstrip("/") + "/"
            url = urljoin(url, path.lstrip("/"))

        return url


class SparkSampler(object):
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

    def _guess_driver_application_master_address(self, process: psutil.Process) -> str:
        """
        Selects the master address for a org.apache.spark.deploy.master.Master running on this node.
        Uses master_address if given, or defaults to my hostname.
        """
        if self._master_address is not None:
            return self._master_address
        else:
            host_name = get_hostname()
            return host_name + ":4040"

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
            spark_cluster_mode = SPARK_DRIVER_MODE
            webapp_url = self._guess_driver_application_master_address(spark_master_process)
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
        while not self._stop_event.is_set():
            if self._spark_sampler is None:
                spark_cluster_conf = self._find_spark_cluster()
                if spark_cluster_conf is not None:
                    master_address, cluster_mode = spark_cluster_conf
                    self._spark_sampler = SparkCollector(cluster_mode, master_address)

            if self._spark_sampler is not None:
                metrics = list(self._spark_sampler.collect())
                timestamp = self._spark_sampler._last_sample_time_ms
                if self._storage_dir is not None:
                    now = get_iso8601_format_time(datetime.now())
                    base_filename = os.path.join(self._storage_dir, f"spark_metric_{escape_filename(now)}")
                    with open(base_filename, "w") as f:
                        json.dump({"timestamp": timestamp, "metrics": metrics}, f)
                if self._client is not None:
                    self._client.submit_spark_metrics(timestamp, metrics)

            self._stop_event.wait(self._sample_period)

    def stop(self) -> None:
        if self._is_running:
            assert self._collection_thread is not None
            self._stop_event.set()
            self._collection_thread.join()
            self._is_running = False

    def is_running(self) -> bool:
        return self._is_running
