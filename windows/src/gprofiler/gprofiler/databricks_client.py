#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import json
import os
import time
from typing import Optional

import requests

from gprofiler.log import get_logger_adapter

HOST_KEY_NAME = "*.sink.ganglia.host"
DATABRICKS_DEPLOY_CONF_PATH = "/databricks/common/conf/deploy.conf"
DATABRICKS_METRICS_PROP_PATH = "/databricks/spark/conf/metrics.properties"
CLUSTER_TAGS_KEY = "spark.databricks.clusterUsageTags.clusterAllTags"
JOB_NAME_KEY = "RunName"
SPARKUI_APPS_URL = "http://{}/api/v1/applications"
REQUEST_TIMEOUT = 5
DEFAULT_WEBUI_PORT = 40001
MAX_RETRIES = 90

logger = get_logger_adapter(__name__)


class DatabricksClient:
    def __init__(self) -> None:
        logger.debug("Getting Databricks job name.")
        self.job_name = self.get_job_name()
        if self.job_name is None:
            logger.warning(
                "Failed initializing Databricks client. Databricks job name will not be included in ephemeral clusters."
            )
        else:
            logger.debug(f"Got Databricks job name: {self.job_name}")

    @staticmethod
    def get_webui_address() -> Optional[str]:
        with open(DATABRICKS_METRICS_PROP_PATH) as f:
            properties = f.read()
        host = dict([line.split("=", 1) for line in properties.splitlines()])[HOST_KEY_NAME]
        return f"{host}:{DEFAULT_WEBUI_PORT}"

    def get_job_name(self) -> Optional[str]:
        # Retry in case of a connection error, as the metrics server might not be up yet.
        for i in range(MAX_RETRIES):
            time.sleep(10)
            try:
                return self._get_job_name_impl()
            except Exception:
                logger.exception("Got Exception while collecting Databricks job name.")
        return None

    def _get_job_name_impl(self) -> Optional[str]:
        # Make sure we're running on a databricks machine
        if not os.path.isfile(DATABRICKS_DEPLOY_CONF_PATH):
            return None
        webui = self.get_webui_address()
        # The API used: https://spark.apache.org/docs/latest/monitoring.html#rest-api
        apps_url = SPARKUI_APPS_URL.format(webui)
        logger.debug(f"Databricks SparkUI address: {apps_url}.")
        resp = requests.get(apps_url, timeout=REQUEST_TIMEOUT)
        if not resp.ok:
            logger.warning(
                f"Failed initializing Databricks client. {apps_url!r} request failed, status_code: {resp.status_code}."
            )
            return None
        apps = resp.json()
        if len(apps) == 0:
            logger.warning("Failed initializing Databricks client. There are no apps.")
            return None
        # There's an assumption that only one app exists, and even if there are more -
        # the name of the job should be the same.
        env_url = f"{apps_url}/{apps[0]['id']}/environment"
        resp = requests.get(env_url, timeout=REQUEST_TIMEOUT)
        if not resp.ok:
            logger.warning(
                f"Failed initializing Databricks client. {env_url!r} request failed, status_code: {resp.status_code}."
            )
            return None
        env = resp.json()
        props = env["sparkProperties"]
        for prop in props:
            if prop[0] == CLUSTER_TAGS_KEY:
                for tag in json.loads(prop[1]):
                    if tag["key"] == JOB_NAME_KEY:
                        return str(tag["value"])
        return None
