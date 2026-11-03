#!/usr/bin/env bash
# Stage 80: navigation data folders, bridge service, map updater, and the limited sudo rights it needs.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
U=$(hu_user)
install -d -o "$U" -g "$U" /var/lib/headunit /var/lib/headunit/maps /var/lib/headunit/routing /var/lib/headunit/search
install -m 644 /opt/headunit/systemd/headunit-update-maps.service /opt/headunit/systemd/headunit-maps-firstrun.service /etc/systemd/system/
systemctl enable headunit-maps-firstrun.service >/dev/null 2>&1 || true   # runs only while a first download is pending
systemctl daemon-reload 2>/dev/null || true   # (no-op when not booted, e.g. image builds)
# The bridge (running as $U) may start the map updater, import a map pack, and change country / time zone /
# language / SSH through bin/hu-system.sh (which checks every argument) - nothing else.
cat > /etc/sudoers.d/headunit <<S
$U ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block headunit-update-maps.service
$U ALL=(root) NOPASSWD: /opt/headunit/bin/hu-import-maps.sh *
$U ALL=(root) NOPASSWD: /opt/headunit/bin/hu-system.sh *
S
chmod 440 /etc/sudoers.d/headunit
visudo -cf /etc/sudoers.d/headunit >/dev/null
enable_user_unit /opt/headunit/systemd/headunit-bridge.service
usermod -aG gpio "$U" 2>/dev/null || true
say "nav: bridge enabled for $U; data in /var/lib/headunit"
