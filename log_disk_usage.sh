echo "$(date -Iseconds) $(/usr/bin/sw_vers -productVersion)" >> /Users/jnatali/repos/mac_disk_usage/data/logged_disk_usage.txt
iostat -I disk0 >> /Users/jnatali/repos/mac_disk_usage/data/logged_disk_usage.txt
