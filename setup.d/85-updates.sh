#!/usr/bin/env bash
# Stage 85: signed software updates from GitHub (see bin/hu-update.py for how they're checked).
# Checks run every 30 min and whenever Wi-Fi connects; an update is only downloaded + verified in
# the background and installs at the next key-on, with automatic rollback if it doesn't start.
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
U=$(hu_user)
LIB=/usr/local/lib/headunit
install -d -m 755 /var/lib/headunit/update /var/cache/headunit/wheels "$LIB"
# The updater runs from a known-good copy outside the version folders. During an update this
# stage leaves it alone; it's promoted only after the new version has started correctly.
if [ "${HU_UPDATE_APPLY:-0}" != 1 ] || [ ! -f "$LIB/hu-update.py" ]; then
  install -m 755 "$HU_ROOT/bin/hu-update.py" "$LIB/hu-update.py"
  install -m 644 "$HU_ROOT/config/release-signers" "$LIB/release-signers"
fi
grep -q '^[^#]*ssh-' "$LIB/release-signers" || warn "config/release-signers has no key yet: updates will be refused until it does"
for u in headunit-update-check.service headunit-update-check.timer headunit-update-now.service \
         headunit-update-apply.service headunit-update-verify.service; do
  install -m 644 "$HU_ROOT/systemd/$u" /etc/systemd/system/
done
install -D -m 755 "$HU_ROOT/bin/hu-nm-dispatch.sh" /etc/NetworkManager/dispatcher.d/90-headunit-update
systemctl daemon-reload 2>/dev/null || true
systemctl enable headunit-update-apply.service headunit-update-verify.service >/dev/null 2>&1
if [ "$(conf_get UPDATES 1)" = 1 ]; then
  systemctl enable headunit-update-check.timer >/dev/null 2>&1
else
  systemctl disable headunit-update-check.timer >/dev/null 2>&1 || true
fi
# Settings > Updates may start a check now - nothing else.
echo "$U ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block headunit-update-now.service" > /etc/sudoers.d/headunit-update
chmod 440 /etc/sudoers.d/headunit-update
visudo -cf /etc/sudoers.d/headunit-update >/dev/null
say "updates: $( [ "$(conf_get UPDATES 1)" = 1 ] && echo on || echo off ), from $(conf_get UPDATE_REPO https://github.com/frozenframework/Pi4B_HeadUnit.git), version $(cat "$HU_ROOT/VERSION")"
