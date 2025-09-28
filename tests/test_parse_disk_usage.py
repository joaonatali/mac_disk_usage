import os
import sys
from pathlib import Path
from unittest.mock import Mock
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import parse_disk_usage as pdu


def test_parse_log_file_success(tmp_path):
    log_content = "\n".join(
        [
            "2025-09-27T10:05:11-05:00",
            "1 2 3 4 5 6 7 8 9",
            "2025-09-27T12:15:42-05:00",
            "9 8 7 6 5 4 3 2 1",
        ]
    )
    log_file = tmp_path / "logged_disk_usage.txt"
    log_file.write_text(log_content)

    parsed = pdu.parse_log_file(log_file)

    assert parsed == [
        ["2025-09-27T10:05:11-05:00", "1", "2", "3", "4", "5", "6", "7", "8", "9"],
        ["2025-09-27T12:15:42-05:00", "9", "8", "7", "6", "5", "4", "3", "2", "1"],
    ]


def test_parse_log_file_invalid_row(tmp_path):
    log_content = "\n".join(
        [
            "2025-09-27T10:05:11-05:00",
            "1 2 3 4 5 6 7 8",  # Only eight data points
        ]
    )
    log_file = tmp_path / "invalid_log.txt"
    log_file.write_text(log_content)

    with pytest.raises(ValueError, match="Wrong number of fields"):
        pdu.parse_log_file(log_file)


def test_create_dataframe_builds_expected_columns():
    parsed_rows = [
        [
            "2025-09-27T10:05:11-05:00",
            "100",
            "200",
            "1024",
            "1",
            "2",
            "97",
            "0.1",
            "0.2",
            "0.3",
        ],
        [
            "2025-09-28T10:05:11-05:00",
            "110",
            "210",
            "3072",
            "3",
            "4",
            "93",
            "0.2",
            "0.3",
            "0.4",
        ],
    ]

    df = pdu.create_dataframe(parsed_rows)

    assert list(df.columns) == [
        "timestamp",
        "KB_by_t",
        "xfrs",
        "disk_usage_since_boot_MB",
        "cpu_us",
        "cpu_sy",
        "cpu_id",
        "load_1m",
        "load_5m",
        "load_15m",
        "delta_disk_usage_GB",
    ]
    assert df["timestamp"].dt.tz is not None
    assert pytest.approx(df.loc[1, "delta_disk_usage_GB"], rel=1e-9) == 2.0
    assert pd.isna(df.loc[0, "delta_disk_usage_GB"])


def test_save_to_ducklake_registers_dataframe(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-09-27T10:05:11Z"]),
            "KB_by_t": [1.0],
            "xfrs": [2.0],
            "disk_usage_since_boot_MB": [1024.0],
            "cpu_us": [3.0],
            "cpu_sy": [4.0],
            "cpu_id": [93.0],
            "load_1m": [0.1],
            "load_5m": [0.2],
            "load_15m": [0.3],
            "delta_disk_usage_GB": [0.0],
        }
    )

    mock_con = Mock()
    mock_con.execute.return_value = None
    connect_mock = Mock(return_value=mock_con)

    catalog_dir = tmp_path / "lake"

    monkeypatch.setattr(pdu.duckdb, "connect", connect_mock)

    pdu.save_to_ducklake(df, catalog_dir)

    connect_mock.assert_called_once_with()

    expected_catalog = os.path.abspath(os.path.join(catalog_dir, "disk_usage.ducklake"))
    attach_calls = [call for call in mock_con.execute.call_args_list if call.args]
    assert any(f"ducklake:{expected_catalog}" in call.args[0] for call in attach_calls)
    mock_con.register.assert_called_once_with("df_view", df)
    mock_con.close.assert_called_once()


def test_save_to_motherduck_with_token(tmp_path, monkeypatch):
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-09-27T10:05:11Z"]),
            "KB_by_t": [1.0],
            "xfrs": [2.0],
            "disk_usage_since_boot_MB": [1024.0],
            "cpu_us": [3.0],
            "cpu_sy": [4.0],
            "cpu_id": [93.0],
            "load_1m": [0.1],
            "load_5m": [0.2],
            "load_15m": [0.3],
            "delta_disk_usage_GB": [0.0],
        }
    )

    mock_con = Mock()
    mock_con.execute.return_value = None
    connect_mock = Mock(return_value=mock_con)
    config = {
        "MOTHERDUCK_TOKEN": "token-value",
        "MOTHERDUCK_DB_NAME": "custom_db",
    }

    catalog_file = tmp_path / "disk_usage.ducklake"

    monkeypatch.setattr(pdu, "load_config", lambda: config)
    monkeypatch.setattr(pdu.duckdb, "connect", connect_mock)
    monkeypatch.delenv("motherduck_token", raising=False)

    pdu.save_to_motherduck(df, str(catalog_file))

    connect_mock.assert_called_once_with(database="md:")
    mock_con.execute.assert_any_call("CREATE DATABASE IF NOT EXISTS custom_db;")
    mock_con.execute.assert_any_call("USE custom_db;")
    assert os.environ["motherduck_token"] == "token-value"
    mock_con.close.assert_called_once()


def test_save_to_motherduck_without_token(monkeypatch):
    connect_mock = Mock()
    monkeypatch.setattr(pdu, "load_config", lambda: {})
    monkeypatch.setattr(pdu.duckdb, "connect", connect_mock)

    df = pd.DataFrame(  # Empty frame with expected schema
        columns=[
            "timestamp",
            "KB_by_t",
            "xfrs",
            "disk_usage_since_boot_MB",
            "cpu_us",
            "cpu_sy",
            "cpu_id",
            "load_1m",
            "load_5m",
            "load_15m",
            "delta_disk_usage_GB",
        ]
    )

    pdu.save_to_motherduck(df, "./data/disk_usage.ducklake")

    connect_mock.assert_not_called()
