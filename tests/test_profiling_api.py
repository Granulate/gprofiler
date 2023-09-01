import datetime
import os
import time
from pathlib import Path
from typing import Any, Callable, List

import pytest as pytest
from docker import DockerClient
from docker.models.images import Image

from gprofiler.client import DEFAULT_PROFILER_SERVER_ADDRESS, DEFAULT_UPLOAD_TIMEOUT, ProfilerAPIClient
from gprofiler.system_metrics import NoopSystemMetricsMonitor
from gprofiler.utils.collapsed_format import parse_one_collapsed
from tests.conftest import AssertInCollapsed
from tests.utils import run_gprofiler_in_container_for_one_session


@pytest.fixture
def profiling_service_name() -> str:
    return os.environ["TEST_SERVICE_NAME"]


@pytest.fixture
def profiling_api_token() -> str:
    return os.environ["TEST_API_TOKEN"]


@pytest.fixture
def hostname() -> str:
    return "upload-test-host"


@pytest.fixture
def server_upload_timeout() -> int:
    return DEFAULT_UPLOAD_TIMEOUT


@pytest.fixture
def profiling_server_address() -> str:
    return DEFAULT_PROFILER_SERVER_ADDRESS


def make_profiler_api_client(
    *,
    api_token: str,
    profiling_service_name: str,
    profiling_server_address: str,
    hostname: str,
    verify_server_ceritificates: bool = True,
    **client_kwargs: Any,
) -> ProfilerAPIClient:
    return ProfilerAPIClient(
        token=api_token,
        service_name=profiling_service_name,
        server_address=profiling_server_address,
        curlify_requests=True,
        hostname=hostname,
        verify=verify_server_ceritificates,
        **client_kwargs,
    )


@pytest.fixture
def profiler_api_client(
    profiling_api_token: str,
    profiling_service_name: str,
    profiling_server_address: str,
    hostname: str,
    server_upload_timeout: int,
) -> ProfilerAPIClient:
    return make_profiler_api_client(
        api_token=profiling_api_token,
        profiling_service_name=profiling_service_name,
        profiling_server_address=profiling_server_address,
        hostname=hostname,
        verify_server_ceritificates=True,
        upload_timeout=server_upload_timeout,
    )


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("server_upload_timeout", [120])
@pytest.mark.parametrize("runtime,profiler_type", [("python", "py-spy")])
def test_upload_profile(
    docker_client: DockerClient,
    application_pid: int,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    assert_collapsed: AssertInCollapsed,
    assert_app_id: Callable,
    profiler_flags: List[str],
    profiler_api_client: ProfilerAPIClient,
) -> None:
    """
    Test successful upload of generated profiles.
    """
    _ = application_pid  # Fixture only used for running the application.
    _ = assert_app_id  # Required for mypy unused argument warning
    spawn_time = time.time()
    local_start_time = datetime.datetime.utcnow()
    collapsed_text = run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, runtime_specific_args, profiler_flags
    )
    local_end_time = datetime.datetime.utcnow()
    collapsed = parse_one_collapsed(collapsed_text)
    # ensure we're uploading valid profile data
    assert_collapsed(collapsed)
    metrics = NoopSystemMetricsMonitor().get_metrics()
    try:
        response_dict = profiler_api_client.submit_profile(
            start_time=local_start_time,
            end_time=local_end_time,
            profile=collapsed_text,
            profile_api_version="v1",
            spawn_time=spawn_time,
            metrics=metrics,
            gpid="",
        )
    except Exception:
        raise
    else:
        assert {"message", "gpid"} <= response_dict.keys() and response_dict["message"] == "ok"
