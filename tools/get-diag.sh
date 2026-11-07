#!/usr/bin/env bash
# Fetch a diagnostics bundle from a head unit over Wi-Fi into this project's diag/ folder (not in git),
# unpacked, so it can be read for debugging:
#
#   tools/get-diag.sh                          make a fresh bundle on miata-headunit.local and fetch it
#   tools/get-diag.sh f250-headunit.local      another head unit
#   tools/get-diag.sh miata-headunit.local --saved
#                                              fetch the bundles already on it (e.g. the ones it made by
#                                              itself after a slow start) instead of making a new one
# Needs: the head unit on the same Wi-Fi with Settings > Developer > SSH login over Wi-Fi on
# (tools/push-maps.py --setup-key saves typing the password each time).
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${1:-miata-headunit.local}; MODE=${2:-new}; U=${HU_SSH_USER:-headunit}
mkdir -p diag
if [ "$MODE" = --saved ]; then
  FILES=$(ssh "$U@$HOST" 'ls -1t /var/lib/headunit/diag/headunit-diag-*.tar.gz 2>/dev/null | head -10')
else
  echo "Collecting diagnostics on $HOST (about a minute)..."
  FILES=$(ssh "$U@$HOST" 'sudo -n /opt/headunit/bin/hu-diag.sh --bundle mac' | tail -1)
fi
[ -n "$FILES" ] || { echo "No diagnostics bundles on $HOST."; exit 1; }
for f in $FILES; do
  n=$(basename "$f")
  if [ ! -f "diag/$n" ]; then scp -q "$U@$HOST:$f" diag/; fi
  tar -xzf "diag/$n" -C diag/
  echo "diag/${n%.tar.gz}/report.txt"
done
