#!/usr/bin/env bash
# Called by udev for USB partitions: mount READ-ONLY under /media/hu/<label> for the music library
# and map packs. A drive labeled HUEXPORT is mounted read-write so test logs can be copied onto it.
set -u
action=$1 dev=/dev/$2
[ -f /etc/headunit/headunit.conf ] && U=$(grep -E '^HU_USER=' /etc/headunit/headunit.conf | cut -d= -f2)
U=${U:-$(getent passwd 1000 | cut -d: -f1)}; UID_=$(id -u "$U"); GID_=$(id -g "$U")
if [ "$action" = remove ]; then
  for mp in /media/hu/*; do
    if ! findmnt -rn "$mp" >/dev/null 2>&1; then rmdir "$mp" 2>/dev/null; fi
  done
  exit 0
fi
eval "$(blkid -o udev -p "$dev")"
fs=${ID_FS_TYPE:-}; label=${ID_FS_LABEL:-$2}
label=$(echo "$label" | tr -c 'A-Za-z0-9._-' '_' | sed 's/_*$//')
mp=/media/hu/$label; [ -e "$mp" ] && findmnt -rn "$mp" >/dev/null && mp=/media/hu/${label}_$2
case "$label" in HUEXPORT*) rw=rw;; *) rw=ro;; esac
case "$fs" in
  vfat|exfat) opts="$rw,nosuid,nodev,noexec,uid=$UID_,gid=$GID_,umask=022,utf8";;
  hfsplus)    opts="ro,nosuid,nodev,noexec,uid=$UID_,gid=$GID_,force";;
  ntfs|ntfs3) fs=ntfs3; opts="ro,nosuid,nodev,noexec,uid=$UID_,gid=$GID_";;
  ext2|ext3|ext4) opts="$rw,nosuid,nodev,noexec";;
  *) logger -t hu-automount "skip $dev ($fs)"; exit 0;;
esac
mkdir -p "$mp"
systemd-mount --no-block --collect -t "$fs" -o "$opts" "$dev" "$mp"
logger -t hu-automount "mounted $dev ($fs, $rw) at $mp"
