#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#


class UnsupportedNamespaceError(Exception):
    def __init__(self, nstype: str):
        super().__init__(f"Namespace {nstype!r} is not supported by this kernel")
        self.nstype = nstype


class CouldNotAcquireMutex(Exception):
    def __init__(self, name) -> None:
        super().__init__(f"Could not acquire mutex {name!r}. Another process might be holding it.")


class CriNotAvailableError(Exception):
    pass


class NoContainerRuntimesError(Exception):
    pass


class ContainerNotFound(Exception):
    def __init__(self, container_id: str) -> None:
        super().__init__(f"Could not find container with id {container_id!r}")


class BadResponseCode(Exception):
    def __init__(self, response_code: int):
        super().__init__(f"Got a bad HTTP response code {response_code}")
