import os
import re
import subprocess
import sys
from pathlib import Path

import polars as pl
import typer
import duckdb
from duckdb import DuckDBPyConnection
from dotenv import dotenv_values

app = typer.Typer(
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


DISK_USAGE_TABLE_COLUMNS: list[str] = [
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


def parse_log_file(file_path: str | Path) -> list[list[str]]:
    """
    Parses the disk usage log file.

    Parameters
    ----------
        file_path: Path to the log file.

    Returns:
        A list of lists, where each inner list represents a row of parsed data.
    """

    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}")
    with open(file_path, "r") as file:
        lines: list[str] = file.readlines()

    parsed_data: list[list[str]] = []
    current_date: str = ""
    current_macos_version: str = ""

    for line in lines:
        line: str = line.strip()
        if date_pattern.match(line):  # Identify timestamps with macos version
            tokens = line.split()
            if len(tokens) != 2:
                raise ValueError(f"Timestamp line missing macos version: {line}")
            current_date = tokens[0]
            current_macos_version = tokens[1]
        elif (
            line and current_date and line[0].isdigit()
        ):  # Data line starts with a digit
            values: list[str] = line.split()
            if len(values) == 9:  # Ensure correct number of data points
                parsed_data.append([current_date] + values + [current_macos_version])
            else:
                raise ValueError(f"Wrong number of fields in line: {values}")

    return parsed_data


def create_dataframe(parsed_data: list[list[str]]) -> pl.DataFrame:
    """
    Create a DataFrame from parsed data.

    Parameters
    ----------
        parsed_data: A list of lists containing the parsed log data.

    Returns:
        A Polars DataFrame with appropriate column names and data types.
    """

    base_columns = DISK_USAGE_TABLE_COLUMNS[:-2]
    columns = base_columns + ["macos_version"]

    df = pl.DataFrame(parsed_data, schema=columns, orient="row")
    numeric_columns = base_columns[1:]

    df: pl.DataFrame = df.with_columns(
        [
            pl.col("timestamp").str.strptime(
                pl.Datetime("us", time_zone="UTC"), strict=False
            ),
            *[
                pl.col(column).cast(pl.Float64, strict=False)
                for column in numeric_columns
            ],
        ]
    )
    df = df.with_columns(pl.col("macos_version").cast(pl.Utf8, strict=False))

    df = df.with_columns(
        [
            (pl.col("disk_usage_since_boot_MB").diff() / 1024).alias(
                "disk_usage_diff_GB"
            ),
            (
                pl.col("timestamp").dt.timestamp(time_unit="us").diff()
                / (24 * 3600 * 1_000_000)
            ).alias("time_diff_days"),
        ]
    )

    df = df.with_columns(
        pl.when(pl.col("time_diff_days").is_null() | (pl.col("time_diff_days") == 0))
        .then(None)
        .otherwise(pl.col("disk_usage_diff_GB") / pl.col("time_diff_days"))
        .alias("delta_disk_usage_GB")
    )

    return df.select(DISK_USAGE_TABLE_COLUMNS)


def save_to_ducklake(
    df: pl.DataFrame,
    catalog_path: str | Path,
    alias: str = "diskusage_lake",
    table_name: str = "disk_usage",
) -> None:
    """
    Persist `df` to a DuckLake catalog so that every write produces
    a new snapshot and older versions remain query-able.

    Parameters
    ----------
    df : polars.DataFrame
        The data to write.
    catalog_path : str
        Path to the DuckLake *metadata* file
        (e.g. '/tmp/disk_usage/metadata.ducklake').
        If you pass a directory instead, a file called
        'disk_usage.ducklake' is created inside it.
    alias : str, optional
        Name used when the catalog is ATTACHed in DuckDB.
    table_name : str, optional
        Target table inside the catalog.
    """
    from manage_ducklake import connect_and_setup_ducklake

    # Resolve paths and create directories -------------------------------------------------
    catalog_path = os.path.abspath(catalog_path)
    if not catalog_path.endswith(".ducklake"):
        catalog_path = os.path.join(catalog_path, "disk_usage.ducklake")
    catalog_dir = os.path.dirname(catalog_path)

    os.makedirs(catalog_dir, exist_ok=True)

    con: DuckDBPyConnection = connect_and_setup_ducklake(catalog_path)

    # Create a new table if it doesn't exist
    con.execute("""
        CREATE TABLE IF NOT EXISTS disk_usage (
            timestamp TIMESTAMP WITH TIME ZONE,
            KB_by_t DOUBLE,
            xfrs DOUBLE,
            disk_usage_since_boot_MB DOUBLE,
            cpu_us DOUBLE,
            cpu_sy DOUBLE,
            cpu_id DOUBLE,
            load_1m DOUBLE,
            load_5m DOUBLE,
            load_15m DOUBLE,
            delta_disk_usage_GB DOUBLE,
            macos_version VARCHAR
        );
    """)
    # Register the DataFrame and commit a new snapshot -------------------------------------
    con.register("df_view", df.to_arrow())
    print(
        f"Attempting to save data to DuckLake: '{catalog_path}', table: '{table_name}'"
    )
    insert_columns = ", ".join(DISK_USAGE_TABLE_COLUMNS)
    con.execute(f"""
                BEGIN TRANSACTION;
                DELETE FROM {table_name};
                INSERT INTO {table_name} ({insert_columns})
                SELECT {insert_columns} FROM df_view;
                COMMIT;
                """)
    con.close()
    print("Data successfully saved to DuckLake.")


def save_to_motherduck(
    df: pl.DataFrame,
    catalog_path: str | Path,
    table_name: str = "disk_usage",
) -> None:
    """
    Save data to a MotherDuck database.

    Parameters
    ----------
    df : polars.DataFrame
        The data to save.
    catalog_path : str
        Path to the DuckLake catalog db.
    """
    config: dict[str, str] = load_config()
    motherduck_token: str | None = config.get("MOTHERDUCK_TOKEN")

    if motherduck_token:
        os.environ["motherduck_token"] = motherduck_token
        motherduck_db_name_from_env: str | None = config.get("MOTHERDUCK_DB_NAME")
        motherduck_db_name: str = (
            "mac_logging"
            if motherduck_db_name_from_env is None
            else motherduck_db_name_from_env
        )

        print(
            f"Attempting to save data to MotherDuck database: '{motherduck_db_name}', table: '{table_name}'"
        )

        con_md: DuckDBPyConnection | None = None
        try:
            con_md = duckdb.connect(database="md:")
            con_md.execute(f"CREATE DATABASE IF NOT EXISTS {motherduck_db_name};")
            con_md.execute(f"USE {motherduck_db_name};")
            con_md.execute(f"ATTACH 'ducklake:{catalog_path}' AS disk_usage_lake;")

            con_md.execute(f"""
                           BEGIN TRANSACTION;
                           DROP TABLE IF EXISTS {table_name};
                           CREATE TABLE {table_name} AS
                           SELECT * FROM disk_usage_lake.{table_name};
                           COMMIT;
                           """)

            print(
                f"Data successfully saved to MotherDuck: {motherduck_db_name}.{table_name}."
            )

        except Exception as e:
            print(f"Failed to save data to MotherDuck: {e}")
        finally:
            if con_md:
                con_md.close()
    elif (
        "MOTHERDUCK_TOKEN" in config
    ):  # Token was in .env but had an empty value (e.g. MOTHERDUCK_TOKEN="")
        print("MOTHERDUCK_TOKEN is configured but empty. Skipping MotherDuck upload.")
    else:  # Token was not in .env
        print(
            "MOTHERDUCK_TOKEN not found in .env configuration. Skipping MotherDuck upload."
        )


def plot_data(df: pl.DataFrame) -> None:
    """
    Plot data in the terminal.

    Parameters
    ----------
    df : pl.DataFrame
        The data to plot.
    """
    df_plot: pl.DataFrame = (
        df.select(["timestamp", "delta_disk_usage_GB"])
        .drop_nulls("delta_disk_usage_GB")
        .filter(pl.col("delta_disk_usage_GB") > 0)
    )

    data_file = "disk_usage_data.txt"
    df_plot.write_csv(
        data_file,
        separator=",",
        datetime_format="%Y-%m-%dT%H:%M:%S",
        include_header=False,
    )

    gnuplot_script = """
    set datafile separator ","
    set terminal dumb size 120, 30
    set title "Daily Disk Usage Over Time (GB)"
    set xlabel "Time"
    set ylabel "Delta Disk Usage (GB)"
    set xdata time
    set timefmt "%Y-%m-%dT%H:%M:%S"
    set format x "%Y-%m-%d"
    plot "{}" using 1:2 with points notitle
    """.format(data_file)

    script_file = "plot_disk_usage.gp"
    with open(script_file, "w") as file:
        file.write(gnuplot_script)

    ret = subprocess.run(["gnuplot", script_file])

    if ret.returncode == 0:
        os.remove(data_file)
        os.remove(script_file)
    else:
        print(f"Gnuplot exited with error code {ret.returncode}", file=sys.stderr)
        print(f"Stdout: {ret.stdout}", file=sys.stderr)
        print(f"Stderr: {ret.stderr}", file=sys.stderr)


def load_config() -> dict[str, str]:
    """Loads .env file and returns a dictionary of non-None values."""
    env_vars: dict[str, str | None] = dotenv_values(".env")
    return {k: v for k, v in env_vars.items() if v is not None}


@app.command()
def main(
    file: Path = typer.Option(
        "./data/logged_disk_usage.txt",
        "--file",
        "-f",
        help="Path to the log file.",
    ),
    save_db: bool = typer.Option(
        False,
        "--save-db",
        "-s",
        help="Save data to DuckLake database.",
    ),
    plot: bool = typer.Option(
        False,
        "--plot",
        "-p",
        help="Plot data in the terminal.",
    ),
    print_df: bool = typer.Option(
        False,
        "--print-df",
        "-d",
        help="Print the DataFrame.",
    ),
    save_md: bool = typer.Option(
        False,
        "--save-md",
        "-m",
        help="Save data to MotherDuck database.",
    ),
    db_path: Path = typer.Option(
        "./data/disk_usage.ducklake",
        "--db-path",
        help="Path to the DuckLake directory.",
    ),
    table_name: str = typer.Option(
        "disk_usage",
        "--table",
        help="Table name to save data to.",
    ),
) -> None:
    """Parse disk usage log and optionally save to DuckLake/MotherDuck and plot data."""

    print("Parsing log file...")
    parsed_data: list[list[str]] = parse_log_file(file)
    df: pl.DataFrame = create_dataframe(parsed_data)

    if print_df:
        print(df)

    if plot:
        plot_data(df)

    if save_db:
        save_to_ducklake(df, catalog_path=db_path, table_name=table_name)

    if save_md:
        save_to_motherduck(df, catalog_path=db_path, table_name=table_name)


if __name__ == "__main__":
    app()
