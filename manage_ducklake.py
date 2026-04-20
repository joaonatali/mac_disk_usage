import os
import duckdb
from duckdb import DuckDBPyConnection, DuckDBPyRelation
import typer
from dotenv import dotenv_values

DUCKLAKE_ALIAS = "diskusage_lake"
env_vars: dict[str, str] = dotenv_values(".env")  # type: ignore
DEFAULT_TABLE_NAME: str = env_vars.get("TABLE_NAME")  # type: ignore


app = typer.Typer(
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


class Ducklake:
    def __init__(self, catalog_path: str) -> None:
        self.catalog_path = catalog_path
        self.con = connect_and_setup_ducklake(self.catalog_path)

    def snapshots(self, min_id: int | None = None) -> duckdb.DuckDBPyRelation:
        """List all available snapshots of a table in the DuckLake catalog.
        Returns a DuckDB relation that can later be converted to a DataFrame"""

        query = f"SELECT * FROM {DUCKLAKE_ALIAS}.snapshots();"
        if min_id:
            snapshots: DuckDBPyRelation = self.con.sql(
                query + f" WHERE snapshot_id > {min_id};"
            )
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
            result: DuckDBPyRelation = self.con.sql(
                query + f" AT (VERSION => {snapshot_id};"
            )
        else:
            result = self.con.sql(query)

        return result

    def prune_history(self, weeks_to_keep: int = 10) -> None:
        """Deletes old snapshots to save space."""

        self.con.execute(
            f"CALL ducklake_expire_snapshots('{DUCKLAKE_ALIAS}', older_than => now() - INTERVAL '{weeks_to_keep} week');"
        )
        self.con.execute(
            f"CALL ducklake_cleanup_old_files('{DUCKLAKE_ALIAS}', cleanup_all => true);"
        )
        self.con.execute(
            f"CALL ducklake_delete_orphaned_files('{DUCKLAKE_ALIAS}', cleanup_all => true);"
        )

    def checkpoint(
        self,
        expire_older_than: str | None = None,
        delete_older_than: str | None = None,
        rewrite_delete_threshold: float | None = None,
    ) -> None:
        """Run DuckLake maintenance through the CHECKPOINT statement."""

        if expire_older_than is not None:
            self.con.execute(
                f"CALL {DUCKLAKE_ALIAS}.set_option('expire_older_than', '{expire_older_than}');"
            )
        if delete_older_than is not None:
            self.con.execute(
                f"CALL {DUCKLAKE_ALIAS}.set_option('delete_older_than', '{delete_older_than}');"
            )
        if rewrite_delete_threshold is not None:
            self.con.execute(
                f"CALL {DUCKLAKE_ALIAS}.set_option('rewrite_delete_threshold', {rewrite_delete_threshold});"
            )

        self.con.execute("CHECKPOINT;")

    def close_con(self) -> None:
        self.con.close()

    def __del__(self) -> None:
        self.close_con()


def connect_and_setup_ducklake(catalog_path: str) -> DuckDBPyConnection:
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

    con: DuckDBPyConnection = duckdb.connect()
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
    dl = Ducklake(catalog_path)
    dl.snapshots().show()


@app.command()
def show_data(
    catalog_path: str,
    table_name: str = DEFAULT_TABLE_NAME,
    snapshot_id: int | None = None,
    max_rows: int = typer.Option(
        20,
        "--max-rows",
        "-n",
        help="Maximum number of rows to display. Use -1 for no limit.",
    ),
) -> None:
    """Shows data from a table, optionally from a specific version."""
    dl = Ducklake(catalog_path)
    rel: DuckDBPyRelation = dl.get_data(table_name, snapshot_id)
    limit = max_rows if max_rows > -1 else None
    rel.show(max_rows=limit)


@app.command()
def prune_history(catalog_path: str, weeks_to_keep: int = 10) -> None:
    """Deletes old snapshots to save space."""

    dl = Ducklake(catalog_path)

    print("Prunning old snapshots...")
    snapshots: DuckDBPyRelation = dl.snapshots()
    to_delete: DuckDBPyRelation = snapshots.filter(
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


@app.command()
def checkpoint(
    catalog_path: str,
    expire_older_than: str | None = typer.Option(
        None,
        help="Set DuckLake expire_older_than before running CHECKPOINT, e.g. '8 week'.",
    ),
    delete_older_than: str | None = typer.Option(
        None,
        help="Set DuckLake delete_older_than before running CHECKPOINT, e.g. '8 week'.",
    ),
    rewrite_delete_threshold: float | None = typer.Option(
        None,
        min=0.0,
        max=1.0,
        help="Set DuckLake rewrite_delete_threshold before running CHECKPOINT.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Run without confirmation prompt.",
    ),
) -> None:
    """Run DuckLake's bundled maintenance flow via CHECKPOINT."""

    dl = Ducklake(catalog_path)
    options: list[str] = []
    if expire_older_than is not None:
        options.append(f"expire_older_than={expire_older_than}")
    if delete_older_than is not None:
        options.append(f"delete_older_than={delete_older_than}")
    if rewrite_delete_threshold is not None:
        options.append(f"rewrite_delete_threshold={rewrite_delete_threshold}")

    print("Running DuckLake CHECKPOINT.")
    if options:
        print("Applying options:")
        for option in options:
            print(f" - {option}")
    else:
        print("Applying DuckLake default checkpoint behavior.")

    if not yes:
        choice = input("\nProceed (y/N)? ")
        if choice.lower() != "y":
            print("Checkpoint not run.")
            return

    dl.checkpoint(
        expire_older_than=expire_older_than,
        delete_older_than=delete_older_than,
        rewrite_delete_threshold=rewrite_delete_threshold,
    )
    print("Checkpoint complete.")


@app.command()
def update_extensions() -> None:
    con: DuckDBPyConnection = duckdb.connect()
    con.sql("UPDATE EXTENSIONS;").show()
    con.close()


if __name__ == "__main__":
    app()
