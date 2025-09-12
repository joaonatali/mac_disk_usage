# /// script
# dependencies = [
#   "duckdb>=1.2.0",
#   "pandas", # For DataFrame manipulation
#   "pytest", # For testing (if tests are present)
#   "python-dotenv", # For loading .env files
#   "typer" # For CLI generation
# ]
# ///

import re
from pathlib import Path
import pandas as pd
import duckdb
import subprocess
import os
import typer
import sys
from dotenv import dotenv_values

app = typer.Typer(
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


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
        lines = file.readlines()

    parsed_data = []
    current_date = None

    for line in lines:
        line = line.strip()
        if date_pattern.match(line):  # Identify timestamps
            current_date = line
        elif (
            line and current_date and line[0].isdigit()
        ):  # Data line starts with a digit
            values = line.split()
            if len(values) == 9:  # Ensure correct number of data points
                parsed_data.append([current_date] + values)
            else:
                raise ValueError(f"Wrong number of fields in line: {values}")

    return parsed_data


def create_dataframe(parsed_data: list[list[str]]) -> pd.DataFrame:
    """
    Create a DataFrame from parsed data.

    Parameters
    ----------
        parsed_data: A list of lists containing the parsed log data.

    Returns:
        A pandas DataFrame with appropriate column names and data types.
    """

    columns = [
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
    ]

    df = pd.DataFrame(parsed_data, columns=columns)
    numeric_columns = columns[1:]
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")

    # Calculate the difference in disk usage and time
    disk_usage_diff_GB = df["disk_usage_since_boot_MB"].diff() / 1024
    time_diff_days = df["timestamp"].diff().dt.total_seconds() / (24 * 3600)

    # Avoid division by zero if time difference is zero
    time_diff_days_no_zeros = time_diff_days.replace(0, pd.NA)

    # Calculate daily average usage, accounting for missing days
    df["delta_disk_usage_GB"] = disk_usage_diff_GB / time_diff_days_no_zeros

    return df


def save_to_ducklake(
    df: pd.DataFrame,
    catalog_path: str | Path,
    alias: str = "diskusage_lake",
    table_name: str = "disk_usage",
) -> None:
    """
    Persist `df` to a DuckLake catalog so that every write produces
    a new snapshot and older versions remain query-able.

    Parameters
    ----------
    df : pandas.DataFrame
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
    import os
    import duckdb

    # Resolve paths and create directories -------------------------------------------------
    catalog_path = os.path.abspath(catalog_path)
    if not catalog_path.endswith(".ducklake"):
        catalog_path = os.path.join(catalog_path, "disk_usage.ducklake")
    catalog_dir = os.path.dirname(catalog_path)

    os.makedirs(catalog_dir, exist_ok=True)

    # Connect & load the DuckLake extension ------------------------------------------------
    con = duckdb.connect()
    con.execute(
        """
        INSTALL ducklake;
        LOAD ducklake;
        """
    )
    # Attach (or re-attach) the catalog ----------------------------------------------------
    con.execute(f"ATTACH IF NOT EXISTS 'ducklake:{catalog_path}' AS {alias};")
    con.execute(f"USE {alias};")

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
            delta_disk_usage_GB DOUBLE
        );
    """)
    # Register the DataFrame and commit a new snapshot -------------------------------------
    con.register("df_view", df)

    con.execute(f"""
                BEGIN TRANSACTION;
                DELETE FROM {table_name};
                INSERT INTO {table_name} SELECT * FROM df_view;
                COMMIT;
                """)
    con.close()


def save_to_motherduck(
    df: pd.DataFrame,
    catalog_path: str | Path,
    table_name: str = "disk_usage",
) -> None:
    """
    Save data to a MotherDuck database.

    Parameters
    ----------
    df : pandas.DataFrame
        The data to save.
    catalog_path : str
        Path to the DuckLake catalog db.
    """
    config: dict[str, str] = load_config()
    motherduck_token = config.get("MOTHERDUCK_TOKEN")

    if motherduck_token:
        os.environ["motherduck_token"] = motherduck_token
        motherduck_db_name_from_env = config.get("MOTHERDUCK_DB_NAME")
        motherduck_db_name = (
            "mac_logging"
            if motherduck_db_name_from_env is None
            else motherduck_db_name_from_env
        )

        print(
            f"Attempting to save data to MotherDuck database: '{motherduck_db_name}', table: '{table_name}'"
        )

        con_md = None
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


def plot_data(df: pd.DataFrame) -> None:
    """
    Plot data in the terminal.

    Parameters
    ----------
    df : pandas.DataFrame
        The data to plot.
    """
    df_plot = df[["timestamp", "delta_disk_usage_GB"]].dropna()
    df_plot = df_plot[df_plot["delta_disk_usage_GB"] > 0]

    data_file = "disk_usage_data.txt"
    df_plot.to_csv(
        data_file, sep=",", index=False, header=False, date_format="%Y-%m-%dT%H:%M:%S"
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
    env_vars = dotenv_values(".env")
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
    parsed_data = parse_log_file(file)
    df = create_dataframe(parsed_data)

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
