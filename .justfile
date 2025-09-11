set dotenv-load

datacatalog := "data/disk_usage.ducklake"
DL_CATALOG := env("DL_CATALOG", datacatalog)
TABLE_NAME := env("TABLE_NAME", "disk_usage")

@_:
    just --list

# Load schedule into launchd
load:
    uv run load_schedule.py

# Parse disk usage
parse:
    uv run parse_disk_usage.py -pd --table {{TABLE_NAME}} --db-path {{DL_CATALOG}}

# Parse disk usage and save data to DuckLake and MotherDuck
save:
    uv run parse_disk_usage.py -pdsm --table {{TABLE_NAME}} --db-path {{DL_CATALOG}}

# Update dependencies and DuckDB extensions
update:
    uv sync -U
    uv run manage_ducklake.py update-extensions

# Show DuckLake snapshots
snapshots:
    uv run manage_ducklake.py snapshots {{DL_CATALOG}}

# Show data in DuckLake
show rows='20':
    uv run manage_ducklake.py show-data {{DL_CATALOG}} --max-rows {{rows}}

# Merge files in DuckLake
merge:
    uv run manage_ducklake.py merge-files {{DL_CATALOG}}

# Prune history from ducklake
prune weeks='10':
    uv run manage_ducklake.py prune-history {{DL_CATALOG}} --weeks-to-keep {{weeks}}
