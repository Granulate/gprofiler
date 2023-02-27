from unittest.mock import mock_open, patch

from gprofiler.metadata.bigdata import BigDataInfo, get_bigdata_info


def test_detect_emr() -> None:
    extra_instance_data_file_content = """
{
  "masterHost": "localhost",
  "releaseLabel": "emr-6.9.0",
  "numCandidates": 1
}
"""

    with patch(
        "gprofiler.metadata.bigdata.emr.open",
        mock_open(read_data=extra_instance_data_file_content),
    ) as mopen:
        assert get_bigdata_info() == BigDataInfo("emr", "emr-6.9.0")
        mopen.assert_called_once_with("/mnt/var/lib/info/extraInstanceData.json", "r")


def test_detect_databricks() -> None:
    version_file_content = "11.3"

    with patch("gprofiler.metadata.bigdata.databricks.open", mock_open(read_data=version_file_content)) as mopen:
        assert get_bigdata_info() == BigDataInfo("databricks", "11.3")
        mopen.assert_called_once_with("/databricks/DBR_VERSION", "r")


def test_detect_dataproc() -> None:
    environment_file_content = """
DATAPROC_IMAGE_TYPE=standard
DATAPROC_IMAGE_VERSION=2.0
DATAPROC_IMAGE_BUILD=20230208-155100-RC01-2_0_deb10_20230127_081410-RC01
"""

    with patch("gprofiler.metadata.bigdata.dataproc.open", mock_open(read_data=environment_file_content)) as mopen:
        assert get_bigdata_info() == BigDataInfo("dataproc", "2.0")
        mopen.assert_called_once_with("/etc/environment", "r")


def test_return_none() -> None:
    assert get_bigdata_info() is None
