import sys
from pathlib import Path
from getpass import getuser
import subprocess
from dotenv import dotenv_values


def run_shell_command(command: str) -> tuple[str, str]:
    """
    Runs a shell command and returns its output.

    Args:
        command: The shell command to execute (string).

    Returns:
        A tuple containing (stdout, stderr) of the command execution.
        Returns (None, str(Exception)) if an error occurs.
    """
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate()
        if process.returncode != 0:
            print(f"Error executing command: {command}", file=sys.stderr)
            print(f"Stderr: {stderr}", file=sys.stderr)
            return None, stderr
        return stdout, stderr
    except Exception as e:
        print(
            f"An exception occurred while trying to run command: {command}",
            file=sys.stderr,
        )
        print(f"Exception: {e}", file=sys.stderr)
        return None, str(e)


class Schedule:
    def __init__(self) -> None:
        self.config = dotenv_values(".env")
        self.user = getuser()

    def create_job_files(self) -> None:
        path_to_scripts = self.config["PATH_TO_SCRIPTS"]
        path_to_logs = self.config["PATH_TO_LOGS"]
        path_to_data = self.config["PATH_TO_DATA"]
        shell = self.config["SHELL"]

        # Create logging script
        with open("./templates/log_disk_usage.template.sh", "r") as file:
            template = file.read()
        script = template.format(
            path_to_data=path_to_data,
        )
        with open(Path(path_to_scripts, "log_disk_usage.sh"), "w") as file:
            file.write(script)

        # Create job template
        with open("./templates/com.diskusagelog.plist", "r") as file:
            template = file.read()
        job = template.format(
            user=self.user,
            shell=shell,
            log_disk_usage=Path(path_to_scripts, "log_disk_usage.sh"),
            log_file=Path(path_to_logs, f"diskusage.log"),
            error_file=Path(path_to_logs, "diskusage.error.log"),
        )
        with open(
            Path(
                "~/Library/LaunchAgents", f"com.{self.user}.diskusagelog.plist"
            ).expanduser(),
            "w",
        ) as file:
            file.write(job)

    def load_into_launchd(self) -> None:
        # Check if job is already loaded
        out, err = run_shell_command(f"launchctl list")
        if err:
            raise RuntimeError("Failed to run command 'launchctl list'")
        if f"com.{self.user}.diskusagelog" in out:
            print("Job is already loaded. Unloading it before loading.")
            out, err = run_shell_command(
                f"launchctl unload ~/Library/LaunchAgents/com.{self.user}.diskusagelog.plist"
            )
            if err:
                raise RuntimeError("Failed to run command 'launchctl unload'")
        # Load scheduled job into Launchd
        out, err = run_shell_command(
            f"launchctl load ~/Library/LaunchAgents/com.{self.user}.diskusagelog.plist"
        )
        if err:
            raise RuntimeError("Failed to run command 'launchctl load'")


if __name__ == "__main__":
    schedule = Schedule()
    schedule.create_job_files()
    schedule.load_into_launchd()
