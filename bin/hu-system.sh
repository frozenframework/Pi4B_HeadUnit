#!/bin/bash
# Root helper for Head Unit Settings (Region & language, Developer). The bridge may run it through
# sudo with exactly these commands; every argument is checked against a fixed list or the system's
# own tables before anything changes.
#   hu-system.sh status
#   hu-system.sh country XX              Wi-Fi country (ISO 3166 code)
#   hu-system.sh timezone Area/City
#   hu-system.sh language CODE           en es zh tl vi ar fr ko pt hi (system locale for the desktop + Hudiy)
#   hu-system.sh ssh on|off
set -euo pipefail
export LC_ALL=C
locale_for() {   # language code -> glibc locale (as listed in /usr/share/i18n/SUPPORTED)
  case "$1" in
    en) echo en_US;; es) echo es_US;; zh) echo zh_CN;; tl) echo tl_PH;; vi) echo vi_VN;;
    ar) echo ar_EG;; fr) echo fr_FR;; ko) echo ko_KR;; pt) echo pt_BR;; hi) echo hi_IN;; *) return 1;;
  esac
}
bad() { echo "{\"ok\": false, \"error\": \"$1\"}"; exit 2; }

case "${1:-}" in
  status)
    tz=$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo "")
    cc=$(grep -o 'cfg80211.ieee80211_regdom=[A-Z]*' /boot/firmware/cmdline.txt 2>/dev/null | cut -d= -f2 || true)
    lang=$(grep -E '^LANG=' /etc/default/locale 2>/dev/null | cut -d= -f2 | tr -d '"' || true)
    ssh=$(systemctl is-enabled ssh 2>/dev/null || true)
    # Is the account still using the default password "headunit"? (checked against the real hash)
    u=$(grep -E '^HU_USER=' /etc/headunit/headunit.conf 2>/dev/null | cut -d= -f2); [ -n "$u" ] || u=$(getent passwd 1000 | cut -d: -f1); def=false
    if [ -n "$u" ]; then
      h=$(getent shadow "$u" | cut -d: -f2)
      if [ -n "$h" ] && python3 -c 'import ctypes, sys
c = ctypes.CDLL("libcrypt.so.1"); c.crypt.restype = ctypes.c_char_p
r = c.crypt(b"headunit", sys.argv[1].encode()); sys.exit(0 if r and r.decode() == sys.argv[1] else 1)' "$h" 2>/dev/null; then def=true; fi
    fi
    printf '{"timezone": "%s", "country": "%s", "locale": "%s", "ssh": %s, "default_password": %s, "user": "%s"}\n' \
      "$tz" "${cc:-US}" "$lang" "$([ "$ssh" = enabled ] && echo true || echo false)" "$def" "$u"
    ;;
  country)
    [[ "${2:-}" =~ ^[A-Z]{2}$ ]] && grep -q "^$2[[:space:]]" /usr/share/zoneinfo/iso3166.tab || bad "unknown country"
    raspi-config nonint do_wifi_country "$2" >/dev/null 2>&1 || bad "couldn't set the Wi-Fi country"
    echo '{"ok": true}';;
  timezone)
    [[ "${2:-}" =~ ^[A-Za-z]+(/[A-Za-z0-9_+-]+){1,2}$ ]] && [ -f "/usr/share/zoneinfo/$2" ] || bad "unknown time zone"
    timedatectl set-timezone "$2" 2>/dev/null || ln -sf "/usr/share/zoneinfo/$2" /etc/localtime || bad "couldn't set the time zone"
    echo "$2" > /etc/timezone
    echo '{"ok": true}';;
  language)
    base=$(locale_for "${2:-}") || bad "unknown language"
    # The UTF-8 entry for it, e.g. "es_US.UTF-8 UTF-8" or "vi_VN UTF-8" (Vietnamese/Hindi have no suffix).
    line=$(grep -m1 -E "^$base(\.UTF-8)? UTF-8$" /usr/share/i18n/SUPPORTED) || bad "the $base locale isn't available"
    loc=${line%% *}
    if ! locale -a 2>/dev/null | grep -qix "$(echo "$loc" | sed 's/UTF-8/utf8/')"; then
      grep -qxF "$line" /etc/locale.gen 2>/dev/null || echo "$line" >> /etc/locale.gen
      locale-gen >/dev/null 2>&1 || bad "couldn't build the $loc locale"
    fi
    update-locale "LANG=$loc"
    echo '{"ok": true, "restart": true}';;
  ssh)
    case "${2:-}" in
      on) raspi-config nonint do_ssh 0 >/dev/null 2>&1 || bad "couldn't start SSH";;
      off) raspi-config nonint do_ssh 1 >/dev/null 2>&1 || bad "couldn't stop SSH";;
      *) bad "use on or off";;
    esac
    echo '{"ok": true}';;
  *) bad "unknown command";;
esac
