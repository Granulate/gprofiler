from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Tuple, Union


@dataclass
class Sample:
    # The field names match the schema expected by the server one-to-one, so we can get a JSON-able
    # dict simply by accessing __dict__.
    labels: Dict[str, str]
    name: str  # metric name
    value: Union[int, float]


@dataclass
class MetricsSnapshot:
    timestamp: datetime
    samples: Tuple[Sample, ...]
