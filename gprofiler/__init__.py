from dataclasses import dataclass

__version__ = "1.1.4"


@dataclass
class Version:
    def __init__(self, version_str: str):
        self.major, self.minor, self.patch = (int(part) for part in version_str.split("."))

    major: int
    minor: int
    patch: int


version = Version(__version__)
