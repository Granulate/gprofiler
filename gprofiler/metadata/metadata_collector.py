import datetime
from pathlib import Path
from typing import Optional

from granulate_utils.linux.ns import run_in_ns
from granulate_utils.metadata import Metadata
from granulate_utils.metadata.bigdata import get_bigdata_info
from granulate_utils.metadata.cloud import get_static_cloud_metadata

from gprofiler import __version__
from gprofiler.gprofiler_types import UserArgs
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.external_metadata import read_external_metadata
from gprofiler.metadata.system_metadata import get_static_system_info

logger = get_logger_adapter(__name__)


def get_static_metadata(spawn_time: float, run_args: UserArgs, external_metadata_path: Optional[Path]) -> Metadata:
    formatted_spawn_time = datetime.datetime.utcfromtimestamp(spawn_time).replace(microsecond=0).isoformat()
    static_system_metadata = get_static_system_info()
    cloud_metadata = get_static_cloud_metadata(logger)
    bigdata = run_in_ns(["mnt"], get_bigdata_info)

    metadata_dict: Metadata = {
        "cloud_provider": cloud_metadata.pop("provider") if cloud_metadata is not None else "unknown",
        "agent_version": __version__,
        "spawn_time": formatted_spawn_time,
    }
    metadata_dict.update(static_system_metadata.__dict__)
    if cloud_metadata is not None:
        metadata_dict["cloud_info"] = cloud_metadata
    metadata_dict["run_arguments"] = run_args
    metadata_dict.update({"big_data": bigdata.__dict__ if bigdata is not None else {}})
    metadata_dict["external_metadata"] = read_external_metadata(external_metadata_path).static
    return metadata_dict


def get_current_metadata(static_metadata: Metadata) -> Metadata:
    current_time = datetime.datetime.utcnow().replace(microsecond=0).isoformat()
    dynamic_metadata = static_metadata
    dynamic_metadata.update({"current_time": current_time})
    return dynamic_metadata
