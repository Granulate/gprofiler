from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EnrichmentOptions:
    """
    Profile enrichment options.
    """

    # profile protocol version. v1 does not support container_names and application_metadata.
    # None means latest
    profile_api_version: Optional[str]
    container_names: bool  # Include container names for each stack in result profile
    application_identifiers: bool  # Attempt to produce & include appid frames for each stack in result profile
    application_identifier_java_args_filters: List[
        str
    ]  # A list of regex filters to add from cmdline arguments of Java to the app id
    application_metadata: bool  # Include specialized metadata per application, e.g for Python - the Python version
