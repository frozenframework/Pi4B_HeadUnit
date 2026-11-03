#!/bin/sh
# Desktop autostart: while first-boot setup is still running, show its log in a terminal window, so the
# plain Raspberry Pi desktop isn't mistaken for "finished" (or "stuck"). Does nothing afterwards.
[ -f /boot/firmware/headunit/headunit.conf ] || exit 0          # setup is done once this file is gone
exec lxterminal --geometry=110x30 --title="Head unit setup: leave it powered on, it reboots by itself" -e sh -c '
  echo "Setting up the head unit: about 20-40 minutes. Do not unplug it; it reboots by itself when done."
  echo "Log: bootfs/headunit/firstboot.log"; echo
  tail -n 25 -f /boot/firmware/headunit/firstboot.log'
