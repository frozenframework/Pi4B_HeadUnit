#!/usr/bin/env bash
# Stage 60: iPod / USB drives mount automatically (read-only) under /media/hu and MPD indexes them by tags.
source /opt/headunit/lib/common.sh
U=$(hu_user); H=$(hu_home)
modprobe hfsplus 2>/dev/null || warn "hfsplus kernel module missing: Mac-formatted iPods won't mount (Windows-formatted ones will)"
install -d /media/hu
install -m 644 /opt/headunit/udev/90-headunit-usb.rules /etc/udev/rules.d/
udevadm control --reload || true

# MPD runs inside the user's session (so it can use PipeWire), not as the system daemon.
systemctl disable --now mpd.service mpd.socket >/dev/null 2>&1 || true
udir "$H/.config/mpd" "$H/.local/share/mpd/playlists"
cat > "$H/.config/mpd/mpd.conf" <<M
music_directory     "/media/hu"
playlist_directory  "$H/.local/share/mpd/playlists"
db_file             "$H/.local/share/mpd/database"
state_file          "$H/.local/share/mpd/state"
sticker_file        "$H/.local/share/mpd/sticker.sql"
bind_to_address     "127.0.0.1"
port                "6600"
auto_update         "no"
restore_paused      "yes"
filesystem_charset  "UTF-8"
audio_output {
    type "pipewire"
    name "Head unit"
}
M
chown -R "$U:$U" "$H/.config/mpd" "$H/.local/share/mpd"
enable_user_unit /usr/lib/systemd/user/mpd.service
say "media: iPod/USB automount + MPD (user service)"
