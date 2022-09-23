#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from typing import Any, Optional, Type, TypeVar

T = TypeVar("T")


def cast_away_optional(arg: Optional[T]) -> T:
    assert arg is not None
    return arg


def assert_cast(typ: Type[T], arg: Any) -> T:
    assert isinstance(arg, typ)
    return arg
