#!/bin/sh
# Desktop autostart, just before Hudiy: wait until the head-unit bridge answers (at most 30 s), so the
# Drive menu apps (Navigation, iPod & USB, Radio, Settings) are reachable when Hudiy loads them.
PORT=$(grep -E '^BRIDGE_PORT=' /etc/headunit/headunit.conf 2>/dev/null | tail -1 | cut -d= -f2); PORT=${PORT:-8765}
i=0
while [ $i -lt 150 ]; do                       # (the bridge normally starts with the system: answers at once)
  curl -fs -o /dev/null -m 1 "http://127.0.0.1:$PORT/apps/common/ping.png" && exit 0
  sleep 0.2; i=$((i+1))
done
exit 0
