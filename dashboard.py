# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "altair==5.5.0",
#     "duckdb==1.4.1",
#     "numpy==2.3.4",
#     "openai==2.6.1",
#     "polars[pyarrow]==1.34.0",
#     "python-dotenv==1.1.1",
#     "python-lsp-ruff==2.3.0",
#     "python-lsp-server==1.13.1",
#     "scikit-learn==1.7.2",
#     "sqlglot==27.28.1",
#     "typer==0.20.0",
#     "websockets==15.0.1",
# ]
# ///

import marimo

__generated_with = "0.18.0"
app = marimo.App(width="medium")

with app.setup(hide_code=True):
    # Initialization code that runs before all other cells
    import marimo as mo
    import polars as pl
    import altair as alt
    # from manage_ducklake import snapshots

    lake_path = "./data/disk_usage.ducklake"


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    # Mac Disk Usage Dash

    Daily logged disk usage on my Mac
    """)
    return


@app.cell(hide_code=True)
def _():
    _df = mo.sql(
        f"""
        INSTALL ducklake;
        ATTACH 'ducklake:{lake_path}' AS disk_usage;
        """
    )
    return


@app.cell
def _():
    snapshots_df = mo.sql(
        f"""
        FROM ducklake_snapshots('disk_usage');
        """
    )
    return (snapshots_df,)


@app.cell(hide_code=True)
def _():
    mo.md(r"""
    ## Data Description


    1. Disk I/O Statistics (disk0)

    This section provides metrics for the specified disk, disk0.

    **`KB/t (Kilobytes per transfer)`**: This is the average amount of data transferred in each I/O operation. In the example, it's 19.97 KB per transfer.

    **`xfrs (Transfers)`**: This is the cumulative number of I/O operations (reads and writes) that have occurred on the disk since boot. In the example, 21,467,560 transfers have happened.

    **`MB (Megabytes)`**: This is the total volume of data, in megabytes, that has been transferred to and from the disk since boot. In the example, 418,697.52 MB (or about 418.7 GB) have been transferred.


    2. CPU Usage Statistics (cpu)

    This section shows how the CPU's time has been spent, as a percentage, averaged since boot.

    **`us (User)`**: The percentage of CPU time spent running user-space processes (e.g., applications, non-kernel tasks). In the example, this is 3%.

    **`sy (System)`**: The percentage of CPU time spent running kernel-space processes (e.g., system calls, kernel-level tasks). In the example, this is 2%.

    **`id (Idle)`**: The percentage of CPU time that the CPU was idle and not waiting for disk I/O. In the example, this is 95%.


    3. System Load Average (load average)

    This section provides the system load average over different time intervals. The load average is a measure of the number of processes that are either currently running or waiting for CPU time.

    **`1m`**: The load average over the last 1 minute. In the example, it's 1.65.

    **`5m`**: The load average over the last 5 minutes. In the example, it's 2.68.

    **`15m`**: The load average over the last 15 minutes. In the example, it's 2.81.
    """)
    return


@app.cell
def _(version_selector):
    full_data = mo.sql(
        f"""
        FROM disk_usage.disk_usage AT (VERSION => {version_selector.value})
        """
    )
    return (full_data,)


@app.cell(hide_code=True)
def _(snapshots_df):
    version_selector = mo.ui.slider(
        start=snapshots_df.select(pl.col("snapshot_id").min()).item(0, 0),
        stop=snapshots_df.select(pl.col("snapshot_id").max()).item(0, 0),
        value=snapshots_df.select(pl.col("snapshot_id").max()).item(0, 0),
        label="Time Travel",
        show_value=True,
    )
    return (version_selector,)


@app.cell(hide_code=True)
def _(full_data, version_selector):
    chart_ts = (
        alt.Chart(data=full_data.filter(pl.col("delta_disk_usage_GB") > 0))
        .mark_point()
        .encode(x="timestamp", y="delta_disk_usage_GB", tooltip="timestamp")
    )

    mo.md(f"""
    ## Disk Usage Time Series

    Daily disk usage

    {version_selector}

    {mo.as_html(chart_ts)}
    """)
    return


@app.cell(hide_code=True)
def _(full_data):
    metrics = [m for m in full_data.columns if (not m.startswith("time"))]
    metric_selector = mo.ui.multiselect(metrics, value=metrics[-1:])
    return (metric_selector,)


@app.cell(hide_code=True)
def _(full_data, metric_selector):
    chart_all = (
        alt.Chart(
            data=full_data.filter(pl.col("delta_disk_usage_GB") > 0).unpivot(
                index="timestamp",
                on=metric_selector.value,
                variable_name="metric",
                value_name="value",
            )
        )
        .mark_point()
        .encode(x="timestamp", y="value", color="metric", tooltip="timestamp")
    )

    mo.md(f"""
    ## Disk Usage All Metrics

    Daily disk usage

    Metrics: {metric_selector}

    {mo.as_html(chart_all)}
    """)
    return


@app.cell(hide_code=True)
def _(full_data):
    chart_coor = (
        alt.Chart(
            data=full_data.with_columns(
                calc_MB=pl.col("KB_by_t") * pl.col("xfrs") / 1024
            )
        )
        .mark_point()
        .encode(x="disk_usage_since_boot_MB", y="calc_MB")
    )

    mo.md(f"""
    ## Calculated disk usage since boot


    {mo.as_html(chart_coor)}
    """)
    return


@app.cell
def _(full_data):
    import numpy as np
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import mean_squared_error, r2_score
    from sklearn.preprocessing import StandardScaler

    # Prepare the data
    df = full_data

    # Handle missing values in target variable
    df_clean = df.filter(
        [
            pl.col("delta_disk_usage_GB").is_not_null(),
            pl.col("delta_disk_usage_GB") > 0,
        ]
    )

    # Define features and target
    feature_columns = [
        col
        for col in df_clean.columns
        if col != "delta_disk_usage_GB"
        and col != "instance"
        and col != "timestamp"
        and col != "xfrs"
        and col != "disk_usage_since_boot_MB"
    ]
    X = df_clean.select(feature_columns)
    y = df_clean.select("delta_disk_usage_GB")

    # Split the data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # Scale the features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Create and train the gradient boosting model
    gb_model = GradientBoostingRegressor(
        n_estimators=100, learning_rate=0.1, max_depth=3, random_state=42
    )

    gb_model.fit(X_train_scaled, y_train)

    # Make predictions
    y_pred = gb_model.predict(X_test_scaled)

    # Calculate metrics
    mse = mean_squared_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    # Display results
    print(f"Mean Squared Error: {mse:.4f}")
    print(f"R² Score: {r2:.4f}")

    # Feature importance
    feature_importance = pl.DataFrame(
        {
            "feature": feature_columns,
            "importance": gb_model.feature_importances_.tolist(),
        }
    ).sort(by="importance", descending=True)

    print("\nFeature Importance:")
    print(feature_importance)
    return X, df_clean, gb_model, scaler


@app.cell(hide_code=True)
def _(X, df_clean, gb_model, scaler):
    pred_chart = (
        alt.Chart(
            data=df_clean.with_columns(
                pl.Series(
                    "predicted", gb_model.predict(scaler.transform(X)).tolist()
                )
            )
        )
        .mark_point()
        .encode(x="delta_disk_usage_GB", y="predicted")
    )

    # Create reference line from (0,0) to (max_x, max_x)
    max_x = df_clean.select(pl.col("delta_disk_usage_GB").max()).item(0, 0)
    reference_line = (
        alt.Chart(data=pl.DataFrame({"x": [0.0, max_x], "y": [0.0, max_x]}))
        .mark_line(color="red", strokeDash=[5, 5])
        .encode(x="x", y="y")
    )

    pred_chart + reference_line

    mo.md(f"""
    ## Predicted vs. actual


    {mo.as_html(pred_chart + reference_line)}
    """)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
