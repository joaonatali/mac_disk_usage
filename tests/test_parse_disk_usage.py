import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
import polars as pl
import pyarrow as pa
import pytest

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import parse_disk_usage as pdu  # noqa: E402


PERSISTED_SCHEMA = [
    (
        column,
        "VARCHAR" if column in {"macos_version", "machine_name"} else "DOUBLE",
        "YES",
    )
    for column in pdu.PERSISTED_DISK_USAGE_TABLE_COLUMNS
]


def persisted_dataframe(machine_name: str = "test-mac") -> pl.DataFrame:
    """Build a representative DataFrame ready for persistence."""
    return pl.DataFrame(
        {
            "timestamp": ["2025-09-27T10:05:11Z"],
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
            "macos_version": ["14.8.3"],
            "machine_name": [machine_name],
        }
    ).with_columns(
        pl.col("timestamp").str.strptime(
            pl.Datetime("us", time_zone="UTC"), strict=False
        )
    )


def test_parse_log_file_success(tmp_path: Path) -> None:
    log_content = "\n".join(
        [
            "2025-09-27T10:05:11-05:00 14.8.3",
            "1 2 3 4 5 6 7 8 9",
            "2025-09-27T12:15:42-05:00 14.8.4",
            "9 8 7 6 5 4 3 2 1",
        ]
    )
    log_file: Path = tmp_path / "logged_disk_usage.txt"
    log_file.write_text(log_content)

    parsed: list[list[str]] = pdu.parse_log_file(log_file)

    assert parsed == [
        [
            "2025-09-27T10:05:11-05:00",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "14.8.3",
        ],
        [
            "2025-09-27T12:15:42-05:00",
            "9",
            "8",
            "7",
            "6",
            "5",
            "4",
            "3",
            "2",
            "1",
            "14.8.4",
        ],
    ]


def test_parse_log_file_single_entry(tmp_path: Path) -> None:
    log_content = "\n".join(
        [
            "2025-09-27T10:05:11-05:00 14.8.3",
            "1 2 3 4 5 6 7 8 9",
        ]
    )
    log_file: Path = tmp_path / "logged_disk_usage.txt"
    log_file.write_text(log_content)

    parsed: list[list[str | None]] = pdu.parse_log_file(log_file)

    assert parsed == [
        [
            "2025-09-27T10:05:11-05:00",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "14.8.3",
        ]
    ]


def test_parse_log_file_invalid_row(tmp_path: Path) -> None:
    log_content = "\n".join(
        [
            "2025-09-27T10:05:11-05:00 14.8.3",
            "1 2 3 4 5 6 7 8",  # Only eight data points
        ]
    )
    log_file: Path = tmp_path / "invalid_log.txt"
    log_file.write_text(log_content)

    with pytest.raises(ValueError, match="Wrong number of fields"):
        pdu.parse_log_file(log_file)


def test_create_dataframe_builds_expected_columns() -> None:
    parsed_rows: list[list[str]] = [
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
            "14.8.3",
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
            "14.8.4",
        ],
    ]

    df: pl.DataFrame = pdu.create_dataframe(parsed_rows)

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
        "macos_version",
    ]
    assert df.schema["timestamp"] == pl.Datetime("us", time_zone="UTC")
    delta_series = df.get_column("delta_disk_usage_GB")
    assert pytest.approx(delta_series[1], rel=1e-9) == 2.0
    assert delta_series[0] is None


def test_get_machine_name_prefers_config(monkeypatch) -> None:
    run_mock = Mock()
    monkeypatch.setattr(pdu.subprocess, "run", run_mock)
    monkeypatch.delenv("MACHINE_NAME", raising=False)

    assert pdu.get_machine_name({"MACHINE_NAME": " private-mac "}) == "private-mac"
    run_mock.assert_not_called()


def test_get_machine_name_falls_back_to_macos_computer_name(monkeypatch) -> None:
    result = Mock(stdout="Office Mac\n")
    monkeypatch.setattr(pdu.subprocess, "run", Mock(return_value=result))
    monkeypatch.delenv("MACHINE_NAME", raising=False)

    assert pdu.get_machine_name({}) == "Office Mac"
    pdu.subprocess.run.assert_called_once_with(
        ["/usr/sbin/scutil", "--get", "ComputerName"],
        capture_output=True,
        check=True,
        text=True,
    )


def test_add_machine_name_builds_persisted_schema() -> None:
    df = pl.DataFrame({column: [] for column in pdu.DISK_USAGE_TABLE_COLUMNS})

    result = pdu.add_machine_name(df, "test-mac")

    assert result.columns == pdu.PERSISTED_DISK_USAGE_TABLE_COLUMNS


def test_save_to_ducklake_registers_dataframe(tmp_path: Path, monkeypatch) -> None:
    df = persisted_dataframe()

    mock_con = Mock()
    mock_con.execute.return_value = mock_con
    mock_con.fetchall.return_value = PERSISTED_SCHEMA
    connect_mock = Mock(return_value=mock_con)

    catalog_dir: Path = tmp_path / "lake"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "disk_usage.ducklake").touch()

    monkeypatch.setattr(pdu.duckdb, "connect", connect_mock)

    pdu.save_to_ducklake(df, catalog_dir)

    connect_mock.assert_called_once_with()

    expected_catalog = os.path.abspath(os.path.join(catalog_dir, "disk_usage.ducklake"))
    attach_calls = [call for call in mock_con.execute.call_args_list if call.args]
    assert any(f"ducklake:{expected_catalog}" in call.args[0] for call in attach_calls)
    assert not any("SET NOT NULL" in call.args[0] for call in attach_calls)
    register_call = mock_con.register.call_args
    assert register_call is not None
    args, kwargs = register_call
    assert args[0] == "df_view"
    assert isinstance(args[1], pa.Table)
    assert "machine_name" in args[1].column_names
    mock_con.close.assert_called_once()


def test_ensure_machine_name_schema_migrates_legacy_table() -> None:
    mock_con = Mock()
    mock_con.execute.return_value = mock_con
    mock_con.fetchall.return_value = [
        row for row in PERSISTED_SCHEMA if row[0] != "machine_name"
    ]

    pdu.ensure_machine_name_schema(mock_con, "disk_usage", "legacy-mac")

    mock_con.execute.assert_any_call(
        "ALTER TABLE disk_usage ADD COLUMN machine_name VARCHAR;"
    )
    mock_con.execute.assert_any_call(
        "UPDATE disk_usage SET machine_name = ? WHERE machine_name IS NULL;",
        ["legacy-mac"],
    )


def test_save_to_motherduck_with_token(tmp_path: Path, monkeypatch) -> None:
    df = persisted_dataframe("test-mac")

    mock_con = Mock()
    mock_con.execute.return_value = mock_con
    mock_con.fetchall.return_value = PERSISTED_SCHEMA
    connect_mock = Mock(return_value=mock_con)
    config = {
        "MOTHERDUCK_TOKEN": "token-value",
        "MOTHERDUCK_DB_NAME": "custom_db",
        "MACHINE_NAME": "test-mac",
    }

    catalog_file: Path = tmp_path / "disk_usage.ducklake"

    monkeypatch.setattr(pdu, "load_config", lambda: config)
    monkeypatch.setattr(pdu.duckdb, "connect", connect_mock)
    monkeypatch.delenv("motherduck_token", raising=False)

    pdu.save_to_motherduck(df, str(catalog_file), machine_name="test-mac")

    connect_mock.assert_called_once_with(database="md:")
    mock_con.execute.assert_any_call("CREATE DATABASE IF NOT EXISTS custom_db;")
    mock_con.execute.assert_any_call("USE custom_db;")
    sql_calls = [call.args[0] for call in mock_con.execute.call_args_list]
    assert not any("DROP TABLE" in sql for sql in sql_calls)
    assert not any("SET NOT NULL" in sql for sql in sql_calls)
    mock_con.execute.assert_any_call(
        "DELETE FROM disk_usage WHERE machine_name = ?;", ["test-mac"]
    )
    assert any(
        "FROM disk_usage_lake.disk_usage" in sql and "WHERE machine_name = ?" in sql
        for sql in sql_calls
    )
    assert os.environ["motherduck_token"] == "token-value"
    mock_con.close.assert_called_once()


def test_save_to_motherduck_without_token(monkeypatch) -> None:
    connect_mock = Mock()
    monkeypatch.setattr(pdu, "load_config", lambda: {})
    monkeypatch.setattr(pdu.duckdb, "connect", connect_mock)

    df = pl.DataFrame()

    pdu.save_to_motherduck(df, "./data/disk_usage.ducklake")

    connect_mock.assert_not_called()


def test_plot_data_exports_gnuplot_friendly_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    df = pl.DataFrame(
        {
            "timestamp": [
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 2, tzinfo=timezone.utc),
            ],
            "delta_disk_usage_GB": [None, 1.5],
            "macos_version": ["14.7.6", "14.7.6"],
        }
    )

    captured: dict[str, object] = {}

    class FakeCompletedProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def fake_run(cmd: list[str]) -> FakeCompletedProcess:
        assert cmd == ["gnuplot", "plot_disk_usage.gp"]
        data_path = tmp_path / "disk_usage_data.txt"
        script_path = tmp_path / "plot_disk_usage.gp"
        captured["data_lines"] = data_path.read_text().splitlines()
        captured["script"] = script_path.read_text()
        return FakeCompletedProcess()

    monkeypatch.setattr(pdu, "subprocess", Mock(run=fake_run))

    pdu.plot_data(df)

    assert captured["data_lines"] == ["2025-01-02T00:00:00,1.5"]
    assert 'plot "disk_usage_data.txt" using 1:2 with points notitle' in captured["script"]  # type: ignore[index]
    assert not (tmp_path / "disk_usage_data.txt").exists()
    assert not (tmp_path / "plot_disk_usage.gp").exists()
