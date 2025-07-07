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
uv run dotenv set PATH_TO_SCRIPTS /Users/you/repos/mac_disk_usage
uv run dotenv set PATH_TO_DATA /Users/you/repos/mac_disk_usage/data
uv run dotenv set PATH_TO_LOGS /Users/you/repos/mac_disk_usage/logs
```

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