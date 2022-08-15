#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

"""
Tests for the logic from gprofiler/merge.py
"""

import pytest

from gprofiler.merge import get_average_frame_count


@pytest.mark.parametrize(
    "samples,count",
    [
        (["a 1"], 1),
        (["d_[k] 1"], 0),
        (["d_[k];e_[k] 1"], 0),
        (["a;b;c;d_[k] 1"], 3),
        (["a;b;c;d_[k];e_[k] 1"], 3),
        (["a 1", "a;b 1"], 1.5),
        (["d_[k] 1", "a;d_[k] 1"], 0.5),
    ],
)
def test_get_average_frame_count(samples: str, count: float) -> None:
    assert get_average_frame_count(samples) == count
