# shellcheck shell=bash
# Shared helpers for setup.sh and its stages. Sourced, not executed.
set -euo pipefail
HU_ROOT=/opt/headunit
HU_CONF=/etc/headunit/headunit.conf
HU_LOG=/var/log/headunit-setup.log
BOOT_CFG=/boot/firmware/config.txt
BOOT_CMDLINE=/boot/firmware/cmdline.txt

say()  { printf '\033[1;36m[headunit]\033[0m %s\n' "$*" | tee -a "$HU_LOG"; }
warn() { printf '\033[1;33m[headunit] WARNING:\033[0m %s\n' "$*" | tee -a "$HU_LOG" >&2; }
die()  { printf '\033[1;31m[headunit] ERROR:\033[0m %s\n' "$*" | tee -a "$HU_LOG" >&2; exit 1; }

# conf_get KEY [default]  -- read a value from /etc/headunit/headunit.conf
conf_get() { local v; v=$(grep -E "^$1=" "$HU_CONF" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'); echo "${v:-${2:-}}"; }

# Replace (or add) a marked block in a file, so running setup twice never duplicates lines.
put_block() {  # file name content
  local file=$1 name=$2 content=$3 tmp
  tmp=$(mktemp)
  touch "$file"
  awk -v s="# >>> headunit:$name" -v e="# <<< headunit:$name" '
    $0==s {skip=1; next} $0==e {skip=0; next} !skip {print}' "$file" > "$tmp"
  printf '%s\n%s\n%s\n' "# >>> headunit:$name" "$content" "# <<< headunit:$name" >> "$tmp"
  cat "$tmp" > "$file"; rm -f "$tmp"
}

hu_user()  { conf_get HU_USER; }
hu_home()  { getent passwd "$(hu_user)" | cut -d: -f6; }
hu_uid()   { id -u "$(hu_user)"; }
as_user()  { sudo -u "$(hu_user)" -H env XDG_RUNTIME_DIR="/run/user/$(hu_uid)" "$@"; }
# Create folders in the user's home AS the user, so no parent (like ~/.config) ends up owned by root.
udir()     { sudo -u "$(hu_user)" mkdir -p "$@"; }

# Enable a user-level systemd unit without needing the user to be logged in right now.
enable_user_unit() {  # unit-file-path
  local unit; unit=$(basename "$1")
  local d; d="$(hu_home)/.config/systemd/user"
  udir "$d/default.target.wants"
  [ "$1" != "$d/$unit" ] && [ -f "$1" ] && install -m 644 -o "$(hu_user)" -g "$(hu_user)" "$1" "$d/$unit"
  local target="$d/$unit"; [ -f "$target" ] || target="/usr/lib/systemd/user/$unit"
  ln -sf "$target" "$d/default.target.wants/$unit"
  chown -h "$(hu_user):$(hu_user)" "$d/default.target.wants/$unit"
}
