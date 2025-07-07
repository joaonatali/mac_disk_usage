set dotenv-load

datacatalog := "data/disk_usage.ducklake"

@_:
    just --list

# Load schedule into launchd
load:
    uv run load_schedule.py

# Parse disk usage
parse:
    uv run parse_disk_usage.py -pd

# Parse disk usage and save data to DuckLake and MotherDuck
save:
    uv run parse_disk_usage.py -pdsm

# Update DuckDB extensions
update:
    uv run manage_ducklake.py update-extensions

# Show DuckLake snapshots
snapshots:
    uv run manage_ducklake.py snapshots {{datacatalog}}

# Show data in DuckLake
show:
    uv run manage_ducklake.py show-data {{datacatalog}}

# Merge files in DuckLake
merge:
    uv run manage_ducklake.py merge-files {{datacatalog}}
