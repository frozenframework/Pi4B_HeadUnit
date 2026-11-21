#!/bin/sh
# Starts Hudiy (its own hudiy_run.sh) with lib/hu-vcf-filter.so loaded: Hudiy then reads the phone's
# contacts without the ones that have neither a number nor an address, and without blank numbers
# (a blank number made Hudiy's call screen show the same wrong name for every caller).
# HUDIY_CONTACTS_FILTER=0 in /etc/headunit/headunit.conf starts Hudiy without it.
SO=/opt/headunit/lib/hu-vcf-filter.so
if [ -f "$SO" ] && ! grep -qsE '^HUDIY_CONTACTS_FILTER="?0' /etc/headunit/headunit.conf; then
  LD_PRELOAD="$SO${LD_PRELOAD:+:$LD_PRELOAD}"; export LD_PRELOAD
fi
exec "$HOME/.hudiy/share/hudiy_run.sh" "$@"
