# Mac Disk Usage Logger

Setup an automated logger for disk usage in your Mac. Allows you to quantify the total amount of disk I/O in your system and prevent excessive usage, which may reduce your SSD life span. 

Uses Apple's launchd to schedule logging. 

## Setup

- Clone the repo and create an environment with `uv`:

```
git clone git@github.com:joaonatali/mac_disk_usage.git
cd mac_disk_usage
uv sync
brew install gnuplot # For in-terminal usage plots
```

- Create a .env file with your desired location for data storage and configs
```
uv run dotenv set PATH_TO_SCRIPTS /Users/you/repos/mac_disk_usage
uv run dotenv set PATH_TO_DATA /Users/you/repos/mac_disk_usage/data
uv run dotenv set PATH_TO_LOGS /Users/you/repos/mac_disk_usage/logs
```

- Setup scheduled job
```
uv run python load_schedule.py
```
