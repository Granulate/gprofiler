#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import uuid
from typing import Optional


# Declare this function here (rather than in utils.py) to avoid circular imports.
def generate_random_id() -> str:
    return str(uuid.uuid4())


class State:
    def __init__(self, run_id: str = None) -> None:
        self._run_id: str = run_id or generate_random_id()
        self._cycle_id: Optional[str] = None

    def set_cycle_id(self, cycle_id: Optional[str]) -> None:
        self._cycle_id = cycle_id

    def init_new_cycle(self) -> None:
        self.set_cycle_id(generate_random_id())

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def cycle_id(self) -> Optional[str]:
        return self._cycle_id


_state: Optional[State] = None


def init_state(run_id: str = None) -> State:
    global _state
    assert _state is None

    _state = State(run_id=run_id)
    return _state


def get_state() -> State:
    assert _state is not None
    return _state
