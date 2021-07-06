from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import requests
from requests import Response

from gprofiler.exceptions import BadResponseCode
from gprofiler.log import get_logger_adapter

METADATA_REQUEST_TIMEOUT = 5

logger = get_logger_adapter(__name__)


@dataclass
class InstanceMetadataBase:
    provider: str


@dataclass()
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
    zone: str
    instance_type: str
    preempted: bool
    preemptible: bool
    instance_id: str
    image_id: str
    name: str


@dataclass
class AzureInstanceMetadata(InstanceMetadataBase):
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
    metadata_response = send_request("http://169.254.169.254/latest/dynamic/instance-identity/document")
    life_cycle_response = send_request("http://169.254.169.254/latest/meta-data/instance-life-cycle")
    if life_cycle_response is None or metadata_response is None:
        return None
    instance_metadata = metadata_response.json()
    region = instance_metadata["region"]
    zone = instance_metadata["availabilityZone"]
    instance_type = instance_metadata["instanceType"]
    account_id = instance_metadata["accountId"]
    image_id = instance_metadata["imageId"]
    instance_id = instance_metadata["instanceId"]
    life_cycle = life_cycle_response.text
    return AwsInstanceMetadata("aws", region, zone, instance_type, life_cycle, account_id, image_id, instance_id)


def get_gcp_metadata() -> Optional[GcpInstanceMetadata]:
    response = send_request(
        "http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true",
        headers={"Metadata-Flavor": "Google"},
    )
    if response is None:
        return None
    instance_metadata = response.json()
    availability_zone = instance_metadata["zone"]
    instance_type = instance_metadata["machineType"]
    preempted = instance_metadata["preempted"] == "TRUE"
    preemptible = instance_metadata["scheduling"]["preemptible"] == "TRUE"
    instance_id = str(instance_metadata["id"])
    image_id = instance_metadata["image"]
    name = instance_metadata["name"]
    return GcpInstanceMetadata(
        provider="gcp",
        zone=availability_zone,
        instance_type=instance_type,
        preemptible=preemptible,
        preempted=preempted,
        instance_id=instance_id,
        image_id=image_id,
        name=name,
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
    subscription_id = instance_metadata["subscriptionId"]
    resource_group_name = instance_metadata["resourceGroupName"]
    resource_id = instance_metadata["resourceId"]
    instance_id = instance_metadata["vmId"]
    name = instance_metadata["name"]
    image_info = None
    storage_profile = instance_metadata.get("storageProfile")
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
        "azure",
        instance_type,
        zone,
        region,
        subscription_id,
        resource_group_name,
        resource_id,
        instance_id,
        name,
        image_info,
    )


def send_request(url: str, headers: Dict[str, str] = None) -> Optional[Response]:
    response = requests.get(url, headers=headers or {}, timeout=METADATA_REQUEST_TIMEOUT)
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
        f"Could not get any cloud instance metadata because of the following exceptions: {formatted_exceptions}"
    )
    return None
