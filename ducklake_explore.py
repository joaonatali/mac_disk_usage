import marimo

__generated_with = "0.13.15"
app = marimo.App(width="medium")

with app.setup:
    # Initialization code that runs before all other cells
    import marimo as mo
    import polars as pl
    import altair as alt

    lake_path = "./data/disk_usage.ducklake"


@app.cell
def _():
    _df = mo.sql(
        f"""
        INSTALL ducklake;
        ATTACH 'ducklake:{lake_path}' AS disk_usage;
        """
    )
    return (disk_usage,)


@app.cell
def _():
    snapshots_df = mo.sql(
        f"""
        FROM ducklake_snapshots('disk_usage');
        """
    )
    return (snapshots_df,)


@app.cell
def _(disk_usage, version):
    full_data = mo.sql(
        f"""
        FROM disk_usage.disk_usage AT (VERSION => {version.value})
        """
    )
    return (full_data,)


@app.cell(hide_code=True)
def _(snapshots_df):
    version = mo.ui.slider(
        start=0,
        stop=snapshots_df.select(pl.col("snapshot_id").max()).item(0, 0),
        value=snapshots_df.select(pl.col("snapshot_id").max()).item(0, 0),
        label="Time Travel",
        show_value=True,
    )
    version
    return (version,)


@app.cell
def _(full_data):
    alt.Chart(
        data=full_data.filter(pl.col("delta_disk_usage_GB") > 0)
    ).mark_point().encode(x="timestamp", y="delta_disk_usage_GB")
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
