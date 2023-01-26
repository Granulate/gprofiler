from typing import Optional


def assert_jvm_flags_equal(actual_jvm_flags: Optional[dict], expected_jvm_flags: dict) -> None:
    assert actual_jvm_flags is not None, "actual_jvm_flags is None"

    assert len(actual_jvm_flags) == len(expected_jvm_flags), "len(actual_jvm_flags) != len(expected_jvm_flags)"

    for actual_flag_name, actual_flag_dict in actual_jvm_flags.items():
        assert actual_flag_name in expected_jvm_flags, f"{actual_flag_name} not in expected_jvm_flags"

        actual_flag_dict.pop("value")
        expected_jvm_flags[actual_flag_name].pop("value")

        assert (
            actual_flag_dict == expected_jvm_flags[actual_flag_name]
        ), f"{actual_flag_dict} != {expected_jvm_flags[actual_flag_name]}"
