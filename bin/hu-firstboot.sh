#!/usr/bin/env bash
# First boot of the ready-to-flash image: Wi-Fi -> Hudiy (on the clean OS) -> head-unit setup -> reboot.
# Log: /boot/firmware/headunit/firstboot.log (readable from your Mac/PC by putting the card back in).
set -uo pipefail
B=/boot/firmware/headunit; LOG=$B/firstboot.log
exec > >(tee -a "$LOG") 2>&1
echo "==== first boot $(date)"
set -a; source "$B/headunit.conf"; set +a

if [ -n "${WIFI_SSID:-}" ]; then
  raspi-config nonint do_wifi_country "${WIFI_COUNTRY:-US}" || true
  nmcli radio wifi on || true
  for i in 1 2 3; do nmcli dev wifi connect "$WIFI_SSID" password "$WIFI_PSK" && break; sleep 10; done
fi
# Phone hotspot as a backup network for software updates. Lower priority, so home Wi-Fi wins.
if [ -n "${HOTSPOT_SSID:-}" ]; then
  nmcli con delete hu-phone-hotspot >/dev/null 2>&1 || true
  nmcli con add type wifi con-name hu-phone-hotspot ssid "$HOTSPOT_SSID" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PSK" \
    connection.autoconnect yes connection.autoconnect-priority -10 && echo "saved phone hotspot '$HOTSPOT_SSID'"
fi
for i in $(seq 1 60); do curl -fsI https://deb.debian.org >/dev/null 2>&1 && break; echo "waiting for internet ($i)"; sleep 5; done

for i in $(seq 1 60); do U=$(getent passwd 1000 | cut -d: -f1); [ -n "$U" ] && break; sleep 5; done
[ -n "${U:-}" ] || { echo "no user account yet; set one in Raspberry Pi Imager (OS customisation)"; exit 1; }
echo "user: $U"

# Hudiy first: it asks for a clean Raspberry Pi OS install.
if [ ! -d "/home/$U/.hudiy" ]; then
  inst=$(find "$B/hudiy" -maxdepth 2 -type f \( -name '*.sh' -o -name '*.deb' -o -name '*.tar.gz' -o -name '*.zip' \) 2>/dev/null | head -1)
  if [ -n "$inst" ]; then
    echo "installing Hudiy from $inst"
    work=$(mktemp -d); chown "$U:$U" "$work"
    case "$inst" in
      *.deb) apt-get install -y "$inst";;
      *.sh) cp "$inst" "$work/"; chown "$U:$U" "$work"/*; sudo -u "$U" -H bash -c "cd '$work' && bash ./$(basename "$inst")";;
      *.tar.gz) tar -xzf "$inst" -C "$work"; chown -R "$U:$U" "$work"; s=$(find "$work" -name '*install*.sh' | head -1); sudo -u "$U" -H bash -c "cd '$(dirname "$s")' && bash '$s'";;
      *.zip) (cd "$work" && python3 -m zipfile -e "$inst" .); chown -R "$U:$U" "$work"; s=$(find "$work" -name '*install*.sh' | head -1); sudo -u "$U" -H bash -c "cd '$(dirname "$s")' && bash '$s'";;
    esac || echo "!! Hudiy installer returned an error. Follow the instructions in Hudiy's email, then run: sudo /opt/headunit/setup.sh --only 90-hudiy"
  else
    echo "!! No Hudiy installer in $B/hudiy - skipping. Install it later, then run: sudo /opt/headunit/setup.sh --only 90-hudiy"
  fi
fi

/opt/headunit/setup.sh --vehicle "${VEHICLE:-f250}" --tier "${TIER:-2}" --region "${MAP_REGION:-texas-plus}" --user "$U" --yes
if [ "${MAPS_AUTO:-1}" = 1 ]; then /opt/headunit/bin/hu-update-maps.sh || echo "map download failed; retry from Settings > Maps"; fi
systemctl disable headunit-firstboot.service
# Don't leave Wi-Fi passwords readable on the card's boot partition (they're saved in NetworkManager now).
sed -E 's/^(WIFI_PSK|HOTSPOT_PSK)=.*/\1="(saved on the Pi)"/' "$B/headunit.conf" > "$B/headunit.conf.applied" && rm -f "$B/headunit.conf"
echo "==== first boot finished $(date); rebooting"
sync; sleep 3; reboot
