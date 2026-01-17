echo "$(date -Iseconds) $(/usr/bin/sw_vers -productVersion)" >> {path_to_data}/logged_disk_usage.txt
iostat -I disk0 >> {path_to_data}/logged_disk_usage.txt
