import json
from typing import Dict, Optional


def _get_instance_data() -> Optional[Dict[str, str]]:
    try:
        with open("/mnt/var/lib/info/extraInstanceData.json", "r") as f:
            obj = json.loads(f.read())
            if isinstance(obj, dict):
                return obj
    except FileNotFoundError:
        pass
    return None


def get_emr_version() -> Optional[str]:
    if (data := _get_instance_data()) is not None:
        release = data.get("releaseLabel")
        if isinstance(release, str):
            return release
    return None
