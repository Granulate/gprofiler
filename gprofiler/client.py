#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import datetime
import gzip
import json
import logging
from io import BytesIO
from typing import Dict

import requests
from requests import Session

from gprofiler.utils import get_iso8061_format_time

logger = logging.getLogger(__name__)

GRANULATE_SERVER_HOST = "https://profiler.granulate.io"
DEFAULT_REQUEST_TIMEOUT = 5
DEFAULT_UPLOAD_TIMEOUT = 120


class APIError(Exception):
    def __init__(self, message: str, full_data: dict = None):
        self.message = message
        self.full_data = full_data

    def __str__(self):
        return self.message


class APIClient:
    BASE_PATH = "api"

    def __init__(self, host: str, key: str, service: str, upload_timeout: int, version: str = "v1"):
        self._host: str = host
        self._upload_timeout = upload_timeout
        self._version: str = version

        self._init_session(key, service)
        logger.info(f"The connection to the server was successfully established (service {service!r})")

    def _init_session(self, key: str, service: str):
        self._session: Session = requests.Session()
        self._session.headers.update({"GPROFILER-API-KEY": key, "GPROFILER-SERVICE-NAME": service})

        # Raises on failure
        self.get_health()

    def get_base_url(self, api_version: str = None) -> str:
        version = api_version if api_version is not None else self._version
        return "{}/{}/{}".format(self._host.rstrip("/"), self.BASE_PATH, version)

    def _send_request(
        self,
        method: str,
        path: str,
        data: Dict,
        files: Dict = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        api_version: str = None,
    ) -> Dict:
        opts: dict = {"headers": {}, "files": files, "timeout": timeout}

        if method.upper() == "GET":
            opts["params"] = data
        else:
            opts["headers"]["Content-Encoding"] = "gzip"
            opts["headers"]["Content-type"] = "application/json"
            buffer = BytesIO()
            with gzip.open(buffer, mode="wt", encoding="utf-8") as gzip_file:
                json.dump(data, gzip_file, ensure_ascii=False)  # type: ignore
            opts["data"] = buffer.getvalue()

        resp = self._session.request(method, "{}/{}".format(self.get_base_url(api_version), path), **opts)
        if 400 <= resp.status_code < 500:
            try:
                data = resp.json()
                raise APIError(data["message"], data)
            except ValueError:
                raise APIError(resp.text)
        else:
            resp.raise_for_status()
        return resp.json()

    def get(self, path: str, data=None, **kwargs) -> Dict:
        return self._send_request("GET", path, data, **kwargs)

    def post(self, path: str, data=None, **kwargs) -> Dict:
        return self._send_request("POST", path, data, **kwargs)

    def put(self, path: str, data=None, **kwargs) -> Dict:
        return self._send_request("PUT", path, data, **kwargs)

    def patch(self, path: str, data=None, **kwargs) -> Dict:
        return self._send_request("PATCH", path, data, **kwargs)

    def delete(self, path: str, data=None, **kwargs) -> Dict:
        return self._send_request("DELETE", path, data, **kwargs)

    def get_health(self):
        return self.get("health_check")

    def submit_profile(
        self, start_time: datetime.datetime, end_time: datetime.datetime, hostname: str, profile: str
    ) -> Dict:
        return self.post(
            "profiles",
            {
                "start_time": get_iso8061_format_time(start_time),
                "end_time": get_iso8061_format_time(end_time),
                "hostname": hostname,
                "profile": profile,
            },
            timeout=self._upload_timeout,
            api_version="v2",
        )
