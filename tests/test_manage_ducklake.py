import sys
from pathlib import Path
from unittest.mock import Mock, call
from duckdb import DuckDBPyRelation, DuckDBPyConnection

import pytest
from typer.testing import CliRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import manage_ducklake as mdl  # noqa: E402


runner = CliRunner()


def test_connect_and_setup_ducklake_missing_file(tmp_path) -> None:
    catalog_path: Path = tmp_path / "missing.ducklake"

    with pytest.raises(FileNotFoundError, match="DuckLake catalog file not found"):
        mdl.connect_and_setup_ducklake(str(catalog_path))


def test_connect_and_setup_ducklake_success(tmp_path, monkeypatch) -> None:
    catalog_path: Path = tmp_path / "disk_usage.ducklake"
    catalog_path.touch()
    catalog_str = str(catalog_path)

    mock_con = Mock()
    connect_mock = Mock(return_value=mock_con)
    monkeypatch.setattr(mdl.duckdb, "connect", connect_mock)

    con: DuckDBPyConnection = mdl.connect_and_setup_ducklake(catalog_str)

    connect_mock.assert_called_once_with()
    assert con is mock_con
    assert mock_con.execute.call_args_list == [
        call("INSTALL ducklake;"),
        call("LOAD ducklake;"),
        call(f"ATTACH IF NOT EXISTS 'ducklake:{catalog_str}' AS {mdl.DUCKLAKE_ALIAS};"),
        call(f"USE {mdl.DUCKLAKE_ALIAS};"),
    ]


def test_ducklake_snapshots_returns_relation(monkeypatch):
    mock_con = Mock()
    expected_relation = Mock()
    mock_con.sql.return_value = expected_relation
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))

    dl = mdl.Ducklake("catalog.ducklake")
    result: DuckDBPyRelation = dl.snapshots()

    mock_con.sql.assert_called_once_with("SELECT * FROM diskusage_lake.snapshots();")
    assert result is expected_relation


def test_ducklake_snapshots_filters_with_min_id(monkeypatch):
    mock_con = Mock()
    expected_relation = Mock()
    mock_con.sql.return_value = expected_relation
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))

    dl = mdl.Ducklake("catalog.ducklake")
    result: DuckDBPyRelation = dl.snapshots(min_id=7)

    mock_con.sql.assert_called_once_with(
        "SELECT * FROM diskusage_lake.snapshots(); WHERE snapshot_id > 7;"
    )
    assert result is expected_relation


def test_ducklake_get_data_latest(monkeypatch) -> None:
    mock_con = Mock()
    expected_relation = Mock()
    mock_con.sql.return_value = expected_relation
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))

    dl = mdl.Ducklake("catalog.ducklake")
    result: DuckDBPyRelation = dl.get_data("custom_table")

    mock_con.sql.assert_called_once_with("SELECT * FROM diskusage_lake.custom_table;")
    assert result is expected_relation


def test_ducklake_get_data_specific_snapshot(monkeypatch) -> None:
    mock_con = Mock()
    expected_relation = Mock()
    mock_con.sql.return_value = expected_relation
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))

    dl = mdl.Ducklake("catalog.ducklake")
    result: DuckDBPyRelation = dl.get_data("custom_table", snapshot_id=4)

    mock_con.sql.assert_called_once_with(
        "SELECT * FROM diskusage_lake.custom_table; AT (VERSION => 4;"
    )
    assert result is expected_relation


def test_ducklake_prune_history_executes_expected_calls(monkeypatch) -> None:
    mock_con = Mock()
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))
    dl = mdl.Ducklake("catalog.ducklake")

    dl.prune_history(weeks_to_keep=5)

    assert mock_con.execute.call_args_list == [
        call(
            "CALL ducklake_expire_snapshots('diskusage_lake', older_than => now() - INTERVAL '5 week');"
        ),
        call("CALL ducklake_cleanup_old_files('diskusage_lake', cleanup_all => true);"),
        call(
            "CALL ducklake_delete_orphaned_files('diskusage_lake', cleanup_all => true);"
        ),
    ]


def test_ducklake_checkpoint_runs_statement(monkeypatch) -> None:
    mock_con = Mock()
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))
    dl = mdl.Ducklake("catalog.ducklake")

    dl.checkpoint()

    mock_con.execute.assert_called_once_with("CHECKPOINT;")


def test_ducklake_checkpoint_sets_requested_options(monkeypatch) -> None:
    mock_con = Mock()
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))
    dl = mdl.Ducklake("catalog.ducklake")

    dl.checkpoint(
        expire_older_than="6 week",
        delete_older_than="3 day",
        rewrite_delete_threshold=0.5,
    )

    assert mock_con.execute.call_args_list == [
        call(
            "CALL diskusage_lake.set_option('expire_older_than', '6 week');"
        ),
        call(
            "CALL diskusage_lake.set_option('delete_older_than', '3 day');"
        ),
        call(
            "CALL diskusage_lake.set_option('rewrite_delete_threshold', 0.5);"
        ),
        call("CHECKPOINT;"),
    ]


def test_ducklake_close_con_closes_connection(monkeypatch) -> None:
    mock_con = Mock()
    monkeypatch.setattr(mdl, "connect_and_setup_ducklake", Mock(return_value=mock_con))
    dl = mdl.Ducklake("catalog.ducklake")

    dl.close_con()

    mock_con.close.assert_called_once_with()


def test_snapshots_cli_shows_relation(monkeypatch, tmp_path) -> None:
    relation = Mock()
    relation.show = Mock()
    dummy_dl = Mock()
    dummy_dl.snapshots.return_value = relation
    monkeypatch.setattr(mdl, "Ducklake", Mock(return_value=dummy_dl))

    result = runner.invoke(mdl.app, ["snapshots", str(tmp_path / "catalog.ducklake")])

    assert result.exit_code == 0
    relation.show.assert_called_once_with()


def test_show_data_cli_supports_unbounded_rows(monkeypatch, tmp_path) -> None:
    relation = Mock()
    relation.show = Mock()
    dummy_dl = Mock()
    dummy_dl.get_data.return_value = relation
    monkeypatch.setattr(mdl, "Ducklake", Mock(return_value=dummy_dl))

    result = runner.invoke(
        mdl.app,
        [
            "show-data",
            str(tmp_path / "catalog.ducklake"),
            "--table-name",
            "custom_table",
            "--snapshot-id",
            "5",
            "--max-rows",
            "-1",
        ],
    )

    assert result.exit_code == 0
    dummy_dl.get_data.assert_called_once_with("custom_table", 5)
    relation.show.assert_called_once_with(max_rows=None)


def test_prune_history_cli_confirms_before_pruning(monkeypatch, tmp_path):
    snapshots_relation = Mock()
    filtered_relation = Mock()
    remaining_relation = Mock()
    snapshots_relation.filter.return_value = filtered_relation
    filtered_relation.show = Mock()
    remaining_relation.show = Mock()

    dummy_dl = Mock()
    dummy_dl.snapshots.side_effect = [snapshots_relation, remaining_relation]
    dummy_dl.prune_history = Mock()
    monkeypatch.setattr(mdl, "Ducklake", Mock(return_value=dummy_dl))
    monkeypatch.setattr("builtins.input", lambda _: "y")

    result = runner.invoke(
        mdl.app,
        [
            "prune-history",
            str(tmp_path / "catalog.ducklake"),
            "--weeks-to-keep",
            "6",
        ],
    )

    assert result.exit_code == 0
    snapshots_relation.filter.assert_called_once_with(
        "snapshot_time < now() - INTERVAL '6 week'"
    )
    filtered_relation.show.assert_called_once_with()
    dummy_dl.prune_history.assert_called_once_with(6)
    remaining_relation.show.assert_called_once_with()
    assert "History prunned. Remaining snapshots:" in result.stdout


def test_prune_history_cli_respects_abort(monkeypatch, tmp_path):
    snapshots_relation = Mock()
    filtered_relation = Mock()
    snapshots_relation.filter.return_value = filtered_relation
    filtered_relation.show = Mock()

    dummy_dl = Mock()
    dummy_dl.snapshots.return_value = snapshots_relation
    dummy_dl.prune_history = Mock()
    monkeypatch.setattr(mdl, "Ducklake", Mock(return_value=dummy_dl))
    monkeypatch.setattr("builtins.input", lambda _: "n")

    result = runner.invoke(
        mdl.app,
        [
            "prune-history",
            str(tmp_path / "catalog.ducklake"),
            "--weeks-to-keep",
            "3",
        ],
    )

    assert result.exit_code == 0
    dummy_dl.prune_history.assert_not_called()
    filtered_relation.show.assert_called_once_with()
    assert "History not prunned." in result.stdout


def test_checkpoint_cli_invokes_ducklake(monkeypatch, tmp_path):
    dummy_dl = Mock()
    monkeypatch.setattr(mdl, "Ducklake", Mock(return_value=dummy_dl))

    result = runner.invoke(
        mdl.app,
        [
            "checkpoint",
            str(tmp_path / "catalog.ducklake"),
            "--expire-older-than",
            "4 week",
            "--delete-older-than",
            "2 day",
            "--rewrite-delete-threshold",
            "0.4",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    dummy_dl.checkpoint.assert_called_once_with(
        expire_older_than="4 week",
        delete_older_than="2 day",
        rewrite_delete_threshold=0.4,
    )


def test_checkpoint_cli_respects_abort(monkeypatch, tmp_path):
    dummy_dl = Mock()
    monkeypatch.setattr(mdl, "Ducklake", Mock(return_value=dummy_dl))
    monkeypatch.setattr("builtins.input", lambda _: "n")

    result = runner.invoke(
        mdl.app,
        [
            "checkpoint",
            str(tmp_path / "catalog.ducklake"),
            "--expire-older-than",
            "4 week",
        ],
    )

    assert result.exit_code == 0
    dummy_dl.checkpoint.assert_not_called()
    assert "Checkpoint not run." in result.stdout


def test_update_extensions_runs_duckdb(monkeypatch) -> None:
    mock_con = Mock()
    mock_relation = Mock()
    mock_con.sql.return_value = mock_relation
    connect_mock = Mock(return_value=mock_con)
    monkeypatch.setattr(mdl.duckdb, "connect", connect_mock)

    mdl.update_extensions()

    connect_mock.assert_called_once_with()
    mock_con.sql.assert_called_once_with("UPDATE EXTENSIONS;")
    mock_relation.show.assert_called_once_with()
    mock_con.close.assert_called_once_with()
