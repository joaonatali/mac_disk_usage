# /// script
# dependencies = [
#   "duckdb>=1.2.0",
#   "pandas", # For DataFrame manipulation
#   "pytest", # For testing (if tests are present)
#   "python-dotenv" # For loading .env files
# ]
# ///

import re
import pandas as pd
import duckdb
import subprocess
import os
import argparse
import datetime
from dotenv import dotenv_values
from typing import Dict


def parse_log_file(file_path: str) -> list[list[str]]:
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
    df["delta_disk_usage_GB"] = df.disk_usage_since_boot_MB.diff() / 1024

    return df


def save_to_ducklake(
    df: pd.DataFrame,
    catalog_path: str,
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
            timestamp TIMESTAMP,
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
    db_path: str,
    config: Dict[str, str],
) -> None:
    motherduck_token = config.get("MOTHERDUCK_TOKEN")

    if motherduck_token:
        os.environ["motherduck_token"] = motherduck_token
        motherduck_db_name_from_env = config.get("MOTHERDUCK_DB_NAME")
        motherduck_db_name = (
            "mac_logging"
            if motherduck_db_name_from_env is None
            else motherduck_db_name_from_env
        )
        md_table_name = "disk_usage_history"

        print(
            f"Attempting to save data to MotherDuck database: '{motherduck_db_name}', table: '{md_table_name}'"
        )

        con_md = None
        try:
            df_md = df.copy()
            # Ensure timestamp is datetime for strftime
            if not pd.api.types.is_datetime64_any_dtype(df_md["timestamp"]):
                df_md["timestamp"] = pd.to_datetime(df_md["timestamp"])
            df_md["ds"] = datetime.datetime.now().strftime("%Y-%m-%d")

            # Connect to MotherDuck
            con_md = duckdb.connect(database="md:")
            con_md.execute(f"CREATE DATABASE IF NOT EXISTS {motherduck_db_name};")
            con_md.execute(f"USE DATABASE {motherduck_db_name};")

            # Register DataFrame as a temporary view for easy querying
            con_md.register("df_view_for_md", df_md)

            # Dynamically create schema DDL string
            cols_for_schema_ddl = []
            for col_name in df_md.columns:
                col_sql_name = f'"{col_name}"'  # Quote all column names

                if col_name == "timestamp":
                    col_type = "TIMESTAMP"
                elif col_name == "ds":  # This is our partition key
                    col_type = "VARCHAR"
                # Numeric columns from pandas (after to_numeric) are likely float64 or int64
                elif pd.api.types.is_integer_dtype(df_md[col_name].dtype):
                    col_type = "BIGINT"
                elif pd.api.types.is_float_dtype(df_md[col_name].dtype):
                    col_type = "DOUBLE"
                elif pd.api.types.is_datetime64_any_dtype(
                    df_md[col_name].dtype
                ):  # Should be caught by 'timestamp'
                    col_type = "TIMESTAMP"
                else:  # Default to VARCHAR for any other types (e.g., object)
                    col_type = "VARCHAR"
                cols_for_schema_ddl.append(f"{col_sql_name} {col_type}")

            schema_ddl_string = ", ".join(cols_for_schema_ddl)

            # Create the new table, partitioned by 'ds'. 'ds' must be one of the columns in schema_ddl_string.
            create_table_sql = f"""
            CREATE TABLE {md_table_name} IF NOT EXISTS ({schema_ddl_string});"""
            con_md.execute(create_table_sql)

            # Insert data into the partitioned table
            con_md.execute(f"INSERT INTO {md_table_name} SELECT * FROM df_view_for_md;")

            print(
                f"Data successfully saved to MotherDuck: {motherduck_db_name}.{md_table_name}."
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
    df_plot = df[["timestamp", "delta_disk_usage_GB"]].dropna()
    df_plot = df_plot[df_plot["delta_disk_usage_GB"] > 0]  # Filter out negative values

    data_file = "disk_usage_data.txt"
    df_plot.to_csv(
        data_file, sep=",", index=False, header=False, date_format="%Y-%m-%dT%H:%M:%S"
    )

    gnuplot_script = """
    set datafile separator ","
    set terminal dumb size 120, 30
    set title "Delta Disk Usage Over Time"
    set xlabel "Time"
    set ylabel "Delta Disk Usage (GB)"
    set xdata time
    set timefmt "%Y-%m-%dT%H:%M:%S"
    set format x "%Y-%m-%d"
    plot "{}" using 1:2 with points
    """.format(data_file)

    script_file = "plot_disk_usage.gp"
    with open(script_file, "w") as file:
        file.write(gnuplot_script)

    ret = subprocess.run(["gnuplot", script_file])

    if ret.returncode == 0:
        os.remove(data_file)
        os.remove(script_file)


def load_config() -> Dict[str, str]:
    """Loads .env file and returns a dictionary of non-None values."""
    env_vars = dotenv_values(".env")
    return {k: v for k, v in env_vars.items() if v is not None}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse disk usage log and optionally save to DuckDB and plot data."
    )
    parser.add_argument(
        "--file",
        type=str,
        default="./data/logged_disk_usage.txt",
        help="Path to the log file.",
    )
    parser.add_argument(
        "-s",
        "--save_db",
        action="store_true",
        help="Save data to DuckLake database.",
    )
    parser.add_argument(
        "-p",
        "--plot",
        action="store_true",
        help="Plot data in the terminal.",
    )
    parser.add_argument(
        "-d",
        "--print_df",
        action="store_true",
        help="Print the DataFrame.",
    )
    parser.add_argument(
        "--db_path",
        type=str,
        default="./data/disk_usage.ducklake",
        help="Path to the DuckLake directory.",
    )

    args = parser.parse_args()

    # config = load_config()  # Load .env config

    parsed_data = parse_log_file(args.file)
    df = create_dataframe(parsed_data)

    if args.save_db:
        save_to_ducklake(df, catalog_path=args.db_path)

    if args.print_df:
        print(df)

    if args.plot:
        plot_data(df)


if __name__ == "__main__":
    main()
