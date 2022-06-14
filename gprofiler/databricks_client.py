#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import json
import os
from typing import Dict, Optional

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

logger = get_logger_adapter(__name__)


class DatabricksClient:
    def __init__(self) -> None:
        self._cluster_deploy_conf_tags: Optional[Dict[str, str]] = None
        self._is_job = False
        try:
            self.job_name = self.get_job_name()
        except Exception:
            self.job_name = None
            logger.warning(
                "Failed initializing Databricks client. Databricks job name will not be included in "
                "ephemeral clusters."
            )

    @property
    def is_databricks_job(self) -> bool:
        return self._is_job

    @staticmethod
    def get_webui_address() -> Optional[str]:
        with open(DATABRICKS_METRICS_PROP_PATH) as f:
            properties = f.read()
        host = dict([line.split('=') for line in properties.splitlines()])[HOST_KEY_NAME]
        return f"{host}:{DEFAULT_WEBUI_PORT}"

    def get_job_name(self) -> Optional[str]:
        # Make sure we're running on a databricks machine
        if not os.path.isfile(DATABRICKS_DEPLOY_CONF_PATH):
            return None
        webui = self.get_webui_address()
        # The API used: https://spark.apache.org/docs/latest/monitoring.html#rest-api
        apps_url = SPARKUI_APPS_URL.format(webui)
        resp = requests.get(apps_url, timeout=REQUEST_TIMEOUT)
        if not resp.ok:
            logger.warning(f"Failed initializing Databricks client. `{apps_url}` request failed with status_code: {resp.status_code}.")
            return None
        apps = resp.json()
        if len(apps) == 0:
            logger.warning("Failed initializing Databricks client. There are no apps.")
            return None
        env_url = f"{apps_url}/{apps[0]['id']}/environment"
        resp = requests.get(env_url, timeout=REQUEST_TIMEOUT)
        if not resp.ok:
            logger.warning(f"Failed initializing Databricks client. `{env_url}` request failed with status_code: {resp.status_code}.")
            return None
        env = resp.json()
        props = env["sparkProperties"]
        for prop in props:
            if prop[0] == CLUSTER_TAGS_KEY:
                for tag in json.loads(prop[1]):
                    if tag["key"] == JOB_NAME_KEY:
                        self._is_job = True
                        return str(tag["value"])
        return None
