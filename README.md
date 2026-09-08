# Mac Disk Usage Logger

Setup an automated logger for disk usage in your Mac. Allows you to quantify the total amount of disk I/O in your system and prevent excessive usage, which may reduce your SSD life span. 

Uses Apple's [launchd](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) to schedule logging.

## Setup

- Clone the repo and create an environment with `uv`:
```
git clone git@github.com:joaonatali/mac_disk_usage.git
cd mac_disk_usage
uv sync
```

- Create a .env file with your desired location for data storage and configs
```
uv run dotenv set PATH_TO_SCRIPTS /Users/{you}/repos/mac_disk_usage
uv run dotenv set PATH_TO_DATA /Users/{you}/repos/mac_disk_usage/data
uv run dotenv set PATH_TO_LOGS /Users/{you}/repos/mac_disk_usage/logs
uv run dotenv set DL_CATALOG /Users/{you}/repos/mac_disk_usage/data/disk_usage.ducklake
uv run dotenv set TABLE_NAME disk_usage
uv run dotenv set MACHINE_NAME your-private-machine-label
```

`MACHINE_NAME` is stored with every row in both DuckLake and MotherDuck. Use a
stable, unique label for each Mac. It may be an alias and does not need to be the
computer's real hostname. The value stays in the untracked `.env` file and must
not be committed to the repository.

If `MACHINE_NAME` is omitted, the logger uses the macOS Computer Name reported
by `scutil --get ComputerName`. On non-macOS systems, or if that value is not
available, it falls back to the system hostname. Setting `MACHINE_NAME` always
overrides these detected values.

- Setup scheduled job
```
uv run python load_schedule.py
```

### Optional

- Install `gnuplot` for in-terminal plotting of logged results
```
brew install gnuplot
```

- Add [MotherDuck](https://motherduck.com/) token to store results in the cloud. 
```
uv run dotenv set MOTHERDUCK_TOKEN your_token
```

### Upgrading an existing single-machine database

Before adding another machine, stop MotherDuck uploads from older copies of the
code and run `just save` once from the original machine. The first save adds the
`machine_name` column to its local DuckLake and the existing MotherDuck table,
then assigns that machine's configured or detected name to all legacy rows.
After verifying the upload, update and enable the remaining machines.

This order matters: the first upgraded machine is assumed to own all rows that
predate the `machine_name` column. Later uploads replace only the rows belonging
to their own machine and leave every other machine's history intact.

## Usage

### Setting up scheduled jobs

(Needs environment variables setup in `.env`)

- Load job in `launchd`:  
    `uv run python load_schedule.py`
- Check job is loaded:  
    `launchctl list | grep -i "diskusagelog"`


### Parse data and load into DBs

- Parse and plot:  
    `uv run parse_disk_usage.py -pd`
- Parse, plot, and save to DuckLake and MotherDuck:  
    `uv run parse_disk_usage.py -pdsm`
- Get help:  
    `uv run parse_disk_usage.py -h`

### Using `just`

Install `just` with `brew install just`

- `just -l`
- `just collect` appends one data point immediately using the same
  `log_disk_usage.sh` script as launchd, without changing the schedule. It uses
  `PATH_TO_SCRIPTS` from `.env`, falling back to the repository directory.
  Run `just load` first to generate the script for your configured data path.
- `just save` parses and saves the collected data to DuckLake and MotherDuck.
