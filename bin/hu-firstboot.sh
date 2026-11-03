#!/usr/bin/env bash
# First boot of the ready-to-flash image: Wi-Fi -> Hudiy (on the clean OS) -> head-unit setup -> reboot.
# Log: /boot/firmware/headunit/firstboot.log (readable from your Mac/PC by putting the card back in).
set -uo pipefail
B=/boot/firmware/headunit; LOG=$B/firstboot.log
exec > >(tee -a "$LOG") 2>&1
echo "==== first boot $(date)"
set -a; source "$B/headunit.conf"; set +a

rfkill unblock wifi 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true
if [ -n "${WIFI_SSID:-}" ]; then
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

# The account made by firstrun.sh (HU_USERNAME), or the one Raspberry Pi Imager's customisation made.
U=""
for i in $(seq 1 60); do
  if [ -n "${HU_USERNAME:-}" ] && id "$HU_USERNAME" >/dev/null 2>&1; then U=$HU_USERNAME; else U=$(getent passwd 1000 | cut -d: -f1); fi
  [ -n "$U" ] && break; sleep 5
done
[ -n "${U:-}" ] || { echo "no user account found (HU_USERNAME in headunit.conf)"; exit 1; }
echo "user: $U"

# Hudiy first: it asks for a clean Raspberry Pi OS install.
# Hudiy's installer (hudiy_installer.tar.gz from the order email) asks two questions on the terminal:
# your order number (checked online: that's the license check) and "reboot now? [y/n]". The order
# number comes from HUDIY_ORDER in headunit.conf; we answer "n" to the reboot (this script reboots later).
run_hudiy() {   # dir script
  if [ -z "${HUDIY_ORDER:-}" ]; then
    rm -rf "/home/$U/hudiy_installer"; cp -r "$1" "/home/$U/hudiy_installer"; chown -R "$U:$U" "/home/$U/hudiy_installer"
    echo "!! HUDIY_ORDER is empty in headunit.conf, so Hudiy was NOT installed. To install it: Settings > Developer >"
    echo "!! Open terminal, then: cd ~/hudiy_installer && ./$2   (enter your order number when asked), reboot, and run:"
    echo "!! sudo /opt/headunit/setup.sh --only 90-hudiy"
    return 1
  fi
  printf '%s\nn\n' "$HUDIY_ORDER" | sudo -u "$U" -H timeout 60m bash -c "cd '$1' && ./$2"
}
if [ ! -d "/home/$U/.hudiy" ]; then
  inst=$(find "$B/hudiy" -maxdepth 2 -type f \( -name '*.sh' -o -name '*.deb' -o -name '*.tar.gz' -o -name '*.zip' \) 2>/dev/null | head -1)
  if [ -n "$inst" ]; then
    echo "installing Hudiy from $(basename "$inst")"
    work=$(mktemp -d); chown "$U:$U" "$work"
    case "$inst" in
      *.deb) apt-get install -y "$inst";;
      *.sh) cp "$inst" "$work/"; chmod +x "$work"/*; chown -R "$U:$U" "$work"; run_hudiy "$work" "$(basename "$inst")";;
      *.tar.gz|*.zip)
        case "$inst" in *.zip) (cd "$work" && python3 -m zipfile -e "$inst" .);; *) tar -xzf "$inst" -C "$work";; esac
        chown -R "$U:$U" "$work"
        s=$(find "$work" -maxdepth 2 -path '*hudiy_installer/run.sh' | head -1)      # Hudiy's own layout
        [ -n "$s" ] || s=$(find "$work" -name '*install*.sh' | head -1)
        [ -n "$s" ] && run_hudiy "$(dirname "$s")" "$(basename "$s")" || echo "!! no run.sh in the Hudiy package";;
    esac
    if [ -d "/home/$U/.hudiy/share" ]; then
      echo "Hudiy installed"
      rm -f "$B"/hudiy/*          # the installer is tied to your purchase: don't leave it on the card
    else
      echo "!! Hudiy didn't install (see the lines above). After fixing it: sudo /opt/headunit/setup.sh --only 90-hudiy"
    fi
    rm -rf "$work"
  else
    echo "!! No Hudiy installer in $B/hudiy - skipping. Install it later, then run: sudo /opt/headunit/setup.sh --only 90-hudiy"
  fi
fi

SETS=()   # optional overrides from this card's headunit.conf (e.g. DISPLAY_TYPE=monitor for bench testing)
for k in DISPLAY_TYPE DISPLAY_ROTATE POWER_MODE; do [ -n "${!k:-}" ] && SETS+=(--set "$k=${!k}"); done
/opt/headunit/setup.sh --vehicle "${VEHICLE:-f250}" --tier "${TIER:-2}" --region "${MAP_REGION:-texas-plus}" --user "$U" "${SETS[@]}" --yes
if [ "${MAPS_AUTO:-1}" = 1 ]; then /opt/headunit/bin/hu-update-maps.sh || echo "map download failed; retry from Settings > Maps"; fi
systemctl disable headunit-firstboot.service
# Don't leave Wi-Fi passwords readable on the card's boot partition (they're saved in NetworkManager now).
sed -E 's/^(WIFI_PSK|HOTSPOT_PSK|HU_PASSWORD|HUDIY_ORDER)=.*/\1="(saved on the Pi)"/' "$B/headunit.conf" > "$B/headunit.conf.applied" && rm -f "$B/headunit.conf"
echo "==== first boot finished $(date); rebooting"
sync; sleep 3; reboot
