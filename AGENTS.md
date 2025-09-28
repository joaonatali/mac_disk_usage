# Mac Disk Usage Logger Guidelines

## Project Purpose
This project automates disk I/O logging on macOS so you can monitor trends, quantify wear, and catch anomalous workloads early. A launchd job runs `log_disk_usage.sh` to collect `iostat` output, and Python CLIs parse, persist, and manage the resulting historical data. DuckLake stores a versioned local catalog, and optional MotherDuck sync keeps a cloud copy in step with local snapshots.

## Key Technologies
- **Python** powers data collection, parsing, and administration scripts.
- **uv** manages the Python environment and locked dependencies defined in `pyproject.toml` and `uv.lock`.
- **just** wraps common workflows so you rarely need to call `uv` directly.
- **DuckDB + DuckLake** provide the analytical storage engine and versioned table layer.
- **Typer** makes the CLIs consistent and self-documenting.
- **launchd** schedules background logging on macOS.
- **Pandas** handles log parsing and transformation.
- **gnuplot** (optional) renders quick terminal plots.
- **MotherDuck** (optional) mirrors DuckLake state to the cloud.

## Project Structure & Module Organization
- **Root scripts**
  - `parse_disk_usage.py` parses `data/logged_disk_usage.txt`, computes deltas, and writes analytics tables.
  - `load_schedule.py` fills the launchd template using `.env` values and loads the job into `launchd`.
  - `manage_ducklake.py` inspects DuckLake history, prunes or merges snapshots, and supports ad-hoc queries.
  - `ducklake_explore.py` launches a marimo dashboard for exploratory analysis.
- **Automation assets**
  - `.justfile` defines the preferred entry points for setup, parsing, and maintenance workflows.
  - `log_disk_usage.sh` is the cron-style script invoked by launchd; keep it lightweight and portable.
  - `templates/com.diskusagelog.plist` is the launchd job template rendered by `load_schedule.py`.
- **Configuration**
  - `.env` (user-supplied) stores paths, DuckLake catalog names, and MotherDuck credentials; never commit secrets.
  - `pyproject.toml` and `uv.lock` pin the Python environment managed by `uv sync`.
- **Data & logs**
  - `data/` houses generated artifacts:
    - `logged_disk_usage.txt` is the append-only raw log captured by `log_disk_usage.sh`.
    - `disk_usage.ducklake` and `disk_usage.ducklake.files/` store the versioned DuckLake catalog.
  - `logs/` collects runtime logs such as `diskusage.log`; rotate or prune during maintenance.
- **Templates & notebooks**
  - `templates/` centralizes plist templates and any future launchd assets.
  - `__marimo__/` caches marimo runtime metadata for the dashboard experience.

## Common Workflows & Commands
Interact with the project through the provided Just recipes:
- **Setup**: `uv sync` installs dependencies; populate `.env` with paths and tokens; run `just load` (or `uv run load_schedule.py`) to render and load the launchd job.
- **Processing**: `just parse` reads `data/logged_disk_usage.txt`, emits plots, and stages DuckLake writes; `just save` performs the parse and persists results to both DuckLake and MotherDuck when configured.
- **Inspection & Maintenance**: `just show` previews table rows, `just snapshots` lists DuckLake history, `just prune weeks=<num>` trims old snapshots, and `just merge` compacts Parquet files; `uv run marimo run ducklake_explore.py` launches the interactive dashboard.
- **Development**: `just test` runs `uv run pytest` for the suite.

## Coding Style & Naming Conventions
Stick to PEP 8 with 4-space indents, exhaustive type hints, and small Typer commands that surface clear error messages. Use descriptive snake_case for modules (`verb_noun.py`) and functions, mirroring the existing CLI structure. Document utilities with NumPy-style docstrings and keep shell snippets portable; prefer configuration pulled from `.env` instead of hard-coded paths.

## Testing Guidelines
Adopt `pytest` for new coverage and place tests under `tests/` as `test_<feature>.py`. Run suites with `just test` (internally `uv run pytest`), and craft fixtures from truncated `logged_disk_usage.txt` samples to capture parsing edge cases. When adding CLI flows, supplement unit tests with manual `just parse` or `just show` checks to confirm DuckLake interactions.

## Commit & Pull Request Guidelines
Write imperative, single-sentence commit subjects (`Add launchctl helper`, `Update duckdb`), keeping related changes squashed. Before opening a PR, ensure `uv lock` reflects dependency updates, scrub sensitive `.env` values from diffs, and note any generated files ignored by Git. PR descriptions should cover problem, approach, linked issue, and validation (command output or screenshots), and request review from maintainers of scheduling or DuckLake modules when touched.

## Environment & Scheduling Notes
Manage secrets with `uv run dotenv set ...` so `.env` stays canonical and untracked. Launchd state can linger; unload stale jobs with `launchctl unload ~/Library/LaunchAgents/com.<user>.diskusagelog.plist` before reloading. Monitor `logs/diskusage.log` growth and rotate or prune during maintenance windows.
