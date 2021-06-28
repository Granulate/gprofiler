import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import requests
from requests import Response

from gprofiler.exceptions import BadResponseCode

AWS_TIMEOUT = 5

logger = logging.getLogger(__name__)


@dataclass
class InstanceMetadataBase:
    provider: str


@dataclass()
class AwsInstanceMetadata(InstanceMetadataBase):
    region: str
    zone: str
    instance_type: str
    life_cycle: str


@dataclass
class GcpInstanceMetadata(InstanceMetadataBase):
    zone: str
    instance_type: str
    preempted: bool
    preemptible: bool


@dataclass
class AzureInstanceMetadata(InstanceMetadataBase):
    instance_type: str
    zone: str
    region: str


def get_aws_metadata() -> Optional[AwsInstanceMetadata]:
    metadata_response = send_request("http://169.254.169.254/latest/dynamic/instance-identity/document")
    life_cycle_response = send_request("http://169.254.169.254/latest/meta-data/instance-life-cycle")
    if life_cycle_response is None or metadata_response is None:
        return None
    instance_metadata = metadata_response.json()
    region = instance_metadata.get("region")
    zone = instance_metadata.get("availabilityZone")
    instance_type = instance_metadata.get("instanceType")
    life_cycle = life_cycle_response.text
    return AwsInstanceMetadata("aws", region, zone, instance_type, life_cycle)


def get_gcp_metadata() -> Optional[GcpInstanceMetadata]:
    response = send_request(
        "http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true",
        headers={"Metadata-Flavor": "Google"},
    )
    if response is None:
        return response
    instance_metadata = response.json()
    availability_zone = instance_metadata["zone"]
    instance_type = instance_metadata["machineType"]
    preempted = instance_metadata["preempted"] == "TRUE"
    preemptible = instance_metadata["scheduling"]["preemptible"]
    return GcpInstanceMetadata(
        zone=availability_zone,
        instance_type=instance_type,
        preemptible=preemptible,
        preempted=preempted,
        provider="gcp",
    )


def get_azure_metadata() -> Optional[AzureInstanceMetadata]:
    response = send_request(
        "http://169.254.169.254/metadata/instance/compute/?api-version=2019-08-15", headers={"Metadata": "true"}
    )
    if response is None:
        return None
    instance_metadata = response.json()
    instance_type = instance_metadata["vmSize"]
    zone = instance_metadata["zone"]
    region = instance_metadata["location"]
    return AzureInstanceMetadata("azure", instance_type, zone, region)


def send_request(url: str, headers: Dict[str, str] = None) -> Optional[Response]:
    response = requests.get(url, headers=headers or {}, timeout=AWS_TIMEOUT)
    if not response.ok:
        raise BadResponseCode(response.status_code)
    return response


def get_cloud_instance_metadata() -> Optional[Dict[str, Union[str, bool, int]]]:
    cloud_metadata_fetchers = [get_aws_metadata, get_gcp_metadata, get_azure_metadata]
    raised_exceptions: List[Exception] = []
    for fetcher in cloud_metadata_fetchers:
        try:
            response = fetcher()
            if response is not None:
                return response.__dict__
        except Exception as exception:
            raised_exceptions.append(exception)
    formatted_exceptions = ', '.join([repr(exception) for exception in raised_exceptions])
    logger.warning(
        f"Could not get any cloud instance metadata because of the following exceptions: " f"{formatted_exceptions}"
    )
    return None
