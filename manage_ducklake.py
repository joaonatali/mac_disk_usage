import os
import duckdb
import typer

DUCKLAKE_ALIAS = "diskusage_lake"
DEFAULT_TABLE_NAME = "disk_usage"

app = typer.Typer(no_args_is_help=True)


class ducklake:
    def __init__(self, catalog_path: str) -> None:
        self.catalog_path = catalog_path
        self.con = connect_and_setup_ducklake(self.catalog_path)

    def snapshots(self, min_id: int | None = None) -> duckdb.DuckDBPyRelation:
        """List all available snapshots of a table in the DuckLake catalog.
        Returns a DuckDB relation that can later be converted to a DataFrame"""

        query = f"SELECT * FROM {DUCKLAKE_ALIAS}.snapshots();"
        if min_id:
            snapshots = self.con.sql(query + f" WHERE snapshot_id > {min_id};")
        else:
            snapshots = self.con.sql(query)

        return snapshots

    def get_data(
        self, table_name: str, snapshot_id: int | None = None
    ) -> duckdb.DuckDBPyRelation:
        """Fetch data from a table, optionally from a specific version.
        Returns a DuckDB relation that can later be converted to a DataFrame"""

        query = f"SELECT * FROM {DUCKLAKE_ALIAS}.{table_name};"
        if snapshot_id is not None:
            result = self.con.sql(query + f" AT (VERSION => {snapshot_id};")
        else:
            result = self.con.sql(query)

        return result

    def prune_history(self, weeks_to_keep: int = 10) -> None:
        """Deletes old snapshots to save space."""

        self.con.execute(
            f"CALL ducklake_expire_snapshots('{DUCKLAKE_ALIAS}', older_than => now() - INTERVAL '{weeks_to_keep} week');"
        )

    def merge_files(self) -> None:
        """Marge small Parquet files to improve performance"""

        self.con.execute(f"CALL {DUCKLAKE_ALIAS}.merge_adjacent_files();")

    def close_con(self) -> None:
        self.con.close()


def connect_and_setup_ducklake(catalog_path: str) -> duckdb.DuckDBPyConnection:
    """
    Connects to DuckDB, loads the DuckLake extension, and attaches the catalog.

    Parameters:
        catalog_file_path: Path to the .ducklake metadata file.
        read_only: Whether to attach the catalog in read-only mode.

    Returns:
        A DuckDB connection object.
    """
    if not os.path.isfile(catalog_path):
        raise FileNotFoundError(
            f"DuckLake catalog file not found: {catalog_path}. "
            "Please provide the direct path to the .ducklake file."
        )
    if not catalog_path.endswith(".ducklake"):
        print(
            f"Warning: Catalog path '{catalog_path}' does not end with .ducklake. "
            "Proceeding with the assumption that it is the correct metadata file."
        )

    con = duckdb.connect()
    try:
        con.execute("INSTALL ducklake;")
        con.execute("LOAD ducklake;")
        attach_sql = (
            f"ATTACH IF NOT EXISTS 'ducklake:{catalog_path}' AS {DUCKLAKE_ALIAS};"
        )
        con.execute(attach_sql)
        con.execute(f"USE {DUCKLAKE_ALIAS};")
    except Exception as e:
        con.close()
        raise RuntimeError(f"Failed to connect or setup DuckLake: {e}")
    return con


@app.command()
def snapshots(catalog_path: str, table_name: str = DEFAULT_TABLE_NAME) -> None:
    """Lists all available versions (snapshots) of a table in the DuckLake catalog."""
    dl = ducklake(catalog_path)
    dl.snapshots().show()


@app.command()
def show_data(
    catalog_path: str,
    table_name: str = DEFAULT_TABLE_NAME,
    snapshot_id: int | None = None,
) -> None:
    """Shows data from a table, optionally from a specific version."""
    dl = ducklake(catalog_path)
    rel = dl.get_data(table_name, snapshot_id)
    rel.show()


@app.command()
def prune_history(catalog_path: str, weeks_to_keep: int = 10) -> None:
    """Deletes old snapshots to save space."""

    dl = ducklake(catalog_path)

    print("Prunning old snapshots...")
    snapshots = dl.snapshots()
    to_delete = snapshots.filter(
        f"snapshot_time < now() - INTERVAL '{weeks_to_keep} week'"
    )
    print("The following snapshots will be deleted:")
    to_delete.show()
    choice = input("\nProceed (y/N)? ")

    if choice.lower() == "y":
        dl.prune_history(weeks_to_keep)
        print("History prunned. Remaining snapshots:")
        dl.snapshots().show()
    else:
        print("History not prunned.")


# @app.command()
# def merge_files()

if __name__ == "__main__":
    app()
