#!/bin/bash
# Desktop autostart, before Hudiy starts: after a software update (or going back to an earlier version)
# empty the web-page caches of Hudiy's built-in browser, so the head-unit apps (map, music, radio,
# settings) load the installed version's files instead of old copies it kept. Runs once per version.
# Only caches are removed (QtWebEngine "Cache" / "Code Cache" / "GPUCache"); settings and logins stay.
set -u
V=$(cat /opt/headunit/VERSION 2>/dev/null || echo unknown)
STAMP="$HOME/.config/headunit/webcache-version"
[ "$(cat "$STAMP" 2>/dev/null)" = "$V" ] && exit 0
pgrep -u "$(id -u)" -f hudiy_run >/dev/null 2>&1 && exit 0       # Hudiy already running: next start
n=0
for root in "$HOME/.cache" "$HOME/.local/share" "$HOME/.hudiy"; do
  [ -d "$root" ] || continue
  while IFS= read -r -d '' d; do
    rm -rf -- "$d" && n=$((n + 1))
  done < <(find "$root" -maxdepth 7 -type d -path '*QtWebEngine*' \( -name Cache -o -name 'Code Cache' -o -name GPUCache \) -prune -print0 2>/dev/null)
done
mkdir -p "$(dirname "$STAMP")" && echo "$V" > "$STAMP"
echo "web caches cleared for version $V ($n folders)"
