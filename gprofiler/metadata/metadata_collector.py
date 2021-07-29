import datetime
from typing import Dict, Union

from gprofiler import __version__
from gprofiler.metadata.cloud_metadata import get_static_cloud_instance_metadata
from gprofiler.metadata.metadata_type import Metadata
from gprofiler.metadata.system_metadata import get_static_system_info


def get_static_metadata(spawn_time: float, run_args: Dict[str, Union[bool, str, int]] = None) -> Metadata:
    formatted_spawn_time = datetime.datetime.utcfromtimestamp(spawn_time).replace(microsecond=0).isoformat()
    static_system_metadata = get_static_system_info()
    cloud_metadata = get_static_cloud_instance_metadata()

    metadata_dict: Metadata = {
        "cloud_provider": cloud_metadata.pop("provider") if cloud_metadata is not None else "unknown",
        "agent_version": __version__,
        "spawn_time": formatted_spawn_time,
    }
    metadata_dict.update(static_system_metadata.__dict__)
    if cloud_metadata is not None:
        metadata_dict["cloud_info"] = cloud_metadata
    if run_args is not None:
        metadata_dict["run_arguments"] = run_args
    return metadata_dict


def get_current_metadata(static_metadata: Metadata) -> Metadata:
    current_time = datetime.datetime.utcnow().replace(microsecond=0).isoformat()
    dynamic_metadata = static_metadata
    dynamic_metadata.update({"current_time": current_time})
    return dynamic_metadata
