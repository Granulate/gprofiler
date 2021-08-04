from dataclasses import dataclass
from http.client import NOT_FOUND
from typing import Dict, List, Optional

import requests
from requests import Response

from gprofiler.exceptions import BadResponseCode
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.metadata_type import Metadata

METADATA_REQUEST_TIMEOUT = 5

logger = get_logger_adapter(__name__)


@dataclass
class InstanceMetadataBase:
    provider: str


@dataclass
class AwsInstanceMetadata(InstanceMetadataBase):
    region: str
    zone: str
    instance_type: str
    life_cycle: str
    account_id: str
    image_id: str
    instance_id: str


@dataclass
class GcpInstanceMetadata(InstanceMetadataBase):
    provider: str
    zone: str
    instance_type: str
    preempted: bool
    preemptible: bool
    instance_id: str
    image_id: str
    name: str


@dataclass
class AzureInstanceMetadata(InstanceMetadataBase):
    provider: str
    instance_type: str
    zone: str
    region: str
    subscription_id: str
    resource_group_name: str
    resource_id: str
    instance_id: str
    name: str
    image_info: Optional[Dict[str, str]]


def get_aws_metadata() -> Optional[AwsInstanceMetadata]:
    # Documentation: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-categories.html
    metadata_response = send_request("http://169.254.169.254/latest/dynamic/instance-identity/document")
    life_cycle_response = send_request("http://169.254.169.254/latest/meta-data/instance-life-cycle")
    if life_cycle_response is None or metadata_response is None:
        return None
    instance = metadata_response.json()
    return AwsInstanceMetadata(
        provider="aws",
        region=instance["region"],
        zone=instance["availabilityZone"],
        instance_type=instance["instanceType"],
        life_cycle=life_cycle_response.text,
        account_id=instance["accountId"],
        image_id=instance["imageId"],
        instance_id=instance["instanceId"],
    )


def get_gcp_metadata() -> Optional[GcpInstanceMetadata]:
    # Documentation: https://cloud.google.com/compute/docs/storing-retrieving-metadata
    response = send_request(
        "http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true",
        headers={"Metadata-Flavor": "Google"},
    )
    if response is None:
        return None
    instance = response.json()
    return GcpInstanceMetadata(
        provider="gcp",
        zone=instance["zone"],
        instance_type=instance["machineType"],
        preemptible=instance["scheduling"]["preemptible"] == "TRUE",
        preempted=instance["preempted"] == "TRUE",
        instance_id=str(instance["id"]),
        image_id=instance["image"],
        name=instance["name"],
    )


def get_azure_metadata() -> Optional[AzureInstanceMetadata]:
    # Documentation: https://docs.microsoft.com/en-us/azure/virtual-machines/linux/instance-metadata-service?tabs=linux
    response = send_request(
        "http://169.254.169.254/metadata/instance/compute/?api-version=2019-08-15", headers={"Metadata": "true"}
    )
    if response is None:
        return None
    instance = response.json()
    image_info = None
    storage_profile = instance.get("storageProfile")
    if isinstance(storage_profile, dict):
        image_reference = storage_profile.get("imageReference")
        if isinstance(image_reference, dict):
            image_info = {
                "image_id": image_reference["id"],
                "image_offer": image_reference["offer"],
                "image_publisher": image_reference["publisher"],
                "image_sku": image_reference["sku"],
                "image_version": image_reference["version"],
            }

    return AzureInstanceMetadata(
        provider="azure",
        instance_type=instance["vmSize"],
        zone=instance["zone"],
        region=instance["location"],
        subscription_id=instance["subscriptionId"],
        resource_group_name=instance["resourceGroupName"],
        resource_id=instance["resourceId"],
        instance_id=instance["vmId"],
        name=instance["name"],
        image_info=image_info,
    )


def send_request(url: str, headers: Dict[str, str] = None) -> Optional[Response]:
    response = requests.get(url, headers=headers or {}, timeout=METADATA_REQUEST_TIMEOUT)
    if response.status_code == NOT_FOUND:
        # It's most likely the wrong cloud provider
        return None
    elif not response.ok:
        raise BadResponseCode(response.status_code)
    return response


def get_static_cloud_instance_metadata() -> Optional[Metadata]:
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
    logger.debug(
        f"Could not get any cloud instance metadata because of the following exceptions: {formatted_exceptions}."
        " The most likely reason is that gProfiler is not installed on a an AWS, GCP or Azure instance."
    )
    return None
