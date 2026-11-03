#!/bin/sh
# NetworkManager dispatcher hook: when Wi-Fi (home or phone hotspot) comes up, look for updates.
case "$2" in
  up|connectivity-change) systemctl start --no-block headunit-update-check.service ;;
esac
exit 0
