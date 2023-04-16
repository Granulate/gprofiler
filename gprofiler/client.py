#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import datetime
import gzip
import json
from io import BytesIO
from typing import IO, TYPE_CHECKING, Any, Dict, List, Optional, Tuple, cast

import requests
from granulate_utils.metrics import MetricsSnapshot
from requests import Session

from gprofiler import __version__
from gprofiler.exceptions import APIError
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.system_metadata import get_hostname
from gprofiler.utils import get_iso8601_format_time, get_iso8601_format_time_from_epoch_time

if TYPE_CHECKING:
    from gprofiler.system_metrics import Metrics

logger = get_logger_adapter(__name__)

DEFAULT_API_SERVER_ADDRESS = "https://api.granulate.io"
DEFAULT_PROFILER_SERVER_ADDRESS = "https://profiler.granulate.io"
DEFAULT_REQUEST_TIMEOUT = 5
DEFAULT_UPLOAD_TIMEOUT = 120


class BaseAPIClient:
    def __init__(
        self,
        curlify_requests: bool,
    ):
        self._curlify = curlify_requests
        self._init_session()

    def _init_session(self) -> None:
        self._session: Session = requests.Session()

    def _get_query_params(self) -> List[Tuple[str, str]]:
        return []

    def _request_url(
        self,
        method: str,
        url: str,
        data: Any,
        files: Dict = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        params: Dict[str, str] = None,
    ) -> Dict:
        opts: dict = {"headers": {}, "files": files, "timeout": timeout}
        if params is None:
            params = {}

        if method.upper() == "GET":
            if data is not None:
                params.update(data)
        else:
            opts["headers"]["Content-Encoding"] = "gzip"
            opts["headers"]["Content-type"] = "application/json"
            buffer = BytesIO()
            with gzip.open(buffer, mode="wt", encoding="utf-8") as gzip_file:
                try:
                    json.dump(data, cast(IO[str], gzip_file), ensure_ascii=False)
                except TypeError:
                    # This should only happen while in development, and is used to get a more indicative error.
                    bad_json = str(data)
                    logger.exception("Given data is not a valid JSON!", bad_json=bad_json)
                    raise
            opts["data"] = buffer.getvalue()

        opts["params"] = self._get_query_params() + [(k, v) for k, v in params.items()]

        resp = self._session.request(method, url, **opts)
        if self._curlify:
            import curlify  # type: ignore  # import here as it's not always required.

            if resp.request.body is not None:
                # curlify attempts to decode bytes into utf-8. our content is gzipped so we undo the gzip here
                # (it's fine to edit the object, as the request was already sent).
                assert resp.request.headers["Content-Encoding"] == "gzip"  # make sure it's really gzip before we undo
                assert isinstance(resp.request.body, bytes)
                resp.request.body = gzip.decompress(resp.request.body)
                del resp.request.headers["Content-Encoding"]
            logger.debug(
                "API request",
                curl_command=curlify.to_curl(resp.request),
                status_code=resp.status_code,
                no_server_log=True,
            )

        if 400 <= resp.status_code < 500:
            try:
                response_data = resp.json()
                raise APIError(response_data.get("message", "(no message in response)"), response_data)
            except ValueError:
                raise APIError(resp.text)
        else:
            resp.raise_for_status()
        return cast(dict, resp.json())


class ProfilerAPIClient(BaseAPIClient):
    BASE_PATH = "api"

    def __init__(
        self,
        *,
        token: str,
        service_name: str,
        server_address: str,
        curlify_requests: bool,
        hostname: str,
        upload_timeout: int,
        verify: bool,
        version: str = "v1",
    ):
        self._server_address = server_address.rstrip("/")
        self._upload_timeout = upload_timeout
        self._version: str = version
        self._key = token
        self._service = service_name
        self._hostname = hostname
        self._verify = verify
        super().__init__(curlify_requests)

    def _init_session(self) -> None:
        self._session: Session = requests.Session()
        self._session.verify = self._verify
        self._session.headers.update({"GPROFILER-API-KEY": self._key, "GPROFILER-SERVICE-NAME": self._service})

        # Raises on failure
        self.get_health()
        logger.info(f"The connection to the server was successfully established (service {self._service!r})")

    def get_base_url(self, api_version: str = None) -> str:
        version = api_version if api_version is not None else self._version
        return "{}/{}/{}".format(self._server_address.rstrip("/"), self.BASE_PATH, version)

    def _get_query_params(self) -> List[Tuple[str, str]]:
        return [
            ("key", self._key),
            ("service", self._service),
            ("hostname", self._hostname),
            ("timestamp", get_iso8601_format_time(datetime.datetime.utcnow())),
        ]

    def _send_request(
        self,
        method: str,
        path: str,
        data: Any,
        files: Dict = None,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        api_version: str = None,
        params: Dict[str, str] = None,
    ) -> Dict:
        return self._request_url(
            method, "{}/{}".format(self.get_base_url(api_version), path), data, files, timeout, params
        )

    def get(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("GET", path, data, **kwargs)

    def post(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("POST", path, data, **kwargs)

    def put(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("PUT", path, data, **kwargs)

    def patch(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("PATCH", path, data, **kwargs)

    def delete(self, path: str, data: Any = None, **kwargs: Any) -> Dict:
        return self._send_request("DELETE", path, data, **kwargs)

    def get_health(self) -> Dict:
        return self.get("health_check")

    def submit_profile(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        profile: str,
        profile_api_version: Optional[str],
        spawn_time: float,
        metrics: "Metrics",
        gpid: str,
    ) -> Dict:
        return self.post(
            "profiles",
            {
                "start_time": get_iso8601_format_time(start_time),
                "end_time": get_iso8601_format_time(end_time),
                "hostname": self._hostname,
                "profile": profile,
                "cpu_avg": metrics.cpu_avg,
                "mem_avg": metrics.mem_avg,
                "spawn_time": get_iso8601_format_time_from_epoch_time(spawn_time),
                "gpid": gpid,
            },
            timeout=self._upload_timeout,
            api_version="v2" if profile_api_version is None else profile_api_version,
            params={"version": __version__},
        )


class APIClient(BaseAPIClient):
    def __init__(
        self,
        token: str,
        service_name: str,
        server_address: str = DEFAULT_API_SERVER_ADDRESS,
        curlify_requests: bool = False,
        timeout: int = DEFAULT_UPLOAD_TIMEOUT,
    ):
        self._token = token
        self._service_name = service_name
        self._server_address = server_address
        self._timeout = timeout
        super().__init__(curlify_requests)

    def _init_session(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._token}",
                "X-Gprofiler-Service": self._service_name,
                "X-GProfiler-Hostname": get_hostname(),
            }
        )

    def submit_spark_metrics(self, snapshot: MetricsSnapshot) -> Dict:
        return self._request_url(
            "POST",
            f"{self._server_address}/telemetry/gprofiler/spark/v1/update",
            bake_metrics_payload(snapshot),
            timeout=self._timeout,
        )


def bake_metrics_payload(snapshot: MetricsSnapshot) -> Dict[str, Any]:
    return {
        "format_version": 1,
        "timestamp": get_iso8601_format_time(snapshot.timestamp),
        "metrics": [sample.__dict__ for sample in snapshot.samples],
    }
