#!/usr/bin/env bash
# =============================================================================================
#  Build ready-to-flash head-unit images from the official Raspberry Pi OS (64-bit, Desktop).
#
#  Mac (Docker Desktop) or Windows (Docker Desktop / WSL2):
#      ./image/build-image.sh --vehicle both [--hudiy ~/Downloads/<hudiy installer>]
#  Linux (as root, or it re-runs itself in Docker):
#      sudo ./image/build-image.sh --vehicle f250
#
#  What it does: downloads the newest Raspberry Pi OS Desktop arm64 image and checks its SHA-256,
#  copies this folder to /opt/headunit, adds a one-time first-boot service, and puts an editable
#  "headunit" folder (settings + Hudiy installer slot) on the boot partition. The OS itself is
#  left untouched until first boot, because Hudiy wants to be installed on a clean system.
#  Output: out/headunit-<vehicle>-<date>.img.xz (+ .sha256) -> flash with Raspberry Pi Imager
#  ("Use custom"), and set user, Wi-Fi and locale in Imager's OS customisation.
# =============================================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VEHICLES=both HUDIY="" BASE_IMG="" INSIDE=0 TIER=2 REGION=texas-plus
while [ $# -gt 0 ]; do
  case "$1" in
    --vehicle) VEHICLES=$2; shift 2;;
    --hudiy) HUDIY=$2; shift 2;;
    --base) BASE_IMG=$2; shift 2;;       # use an already-downloaded .img or .img.xz
    --tier) TIER=$2; shift 2;;
    --region) REGION=$2; shift 2;;
    --inside) INSIDE=1; shift;;
    *) echo "unknown option $1"; exit 1;;
  esac
done
[ "$VEHICLES" = both ] && VEHICLES="f250 miata"
[ "$VEHICLES" = all ] && VEHICLES="f250 miata frontier"

# Not root on Linux (or on a Mac)? Run the same script inside a privileged Debian container.
if [ "$INSIDE" -eq 0 ] && { [ "$(uname -s)" != Linux ] || [ "$(id -u)" -ne 0 ]; }; then
  command -v docker >/dev/null || { echo "Needs Docker Desktop (Mac/Windows) or root on Linux."; exit 1; }
  extra=()
  if [ -n "$HUDIY" ]; then extra+=(-v "$(cd "$(dirname "$HUDIY")" && pwd)/$(basename "$HUDIY"):/hudiy/$(basename "$HUDIY"):ro"); HUDIY=/hudiy/$(basename "$HUDIY"); fi
  if [ -n "$BASE_IMG" ]; then extra+=(-v "$(cd "$(dirname "$BASE_IMG")" && pwd)/$(basename "$BASE_IMG"):/base/$(basename "$BASE_IMG"):ro"); BASE_IMG=/base/$(basename "$BASE_IMG"); fi
  exec docker run --rm --privileged -v "$HERE":/work "${extra[@]}" -w /work debian:trixie \
    bash -c "apt-get update -qq && apt-get install -y -qq curl xz-utils rsync fdisk mtools e2fsprogs util-linux ca-certificates >/dev/null && \
             bash image/build-image.sh --inside --vehicle '${VEHICLES// /,}' --tier $TIER --region $REGION ${HUDIY:+--hudiy $HUDIY} ${BASE_IMG:+--base $BASE_IMG}"
fi
VEHICLES=${VEHICLES//,/ }
OUT="$HERE/out"; mkdir -p "$OUT/cache"

# 1) Base image: official Raspberry Pi OS (64-bit) with desktop, verified against its .sha256.
if [ -z "$BASE_IMG" ]; then
  url=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://downloads.raspberrypi.com/raspios_arm64_latest)
  BASE_IMG="$OUT/cache/$(basename "$url")"
  [ -f "$BASE_IMG" ] || { echo "downloading $url"; curl -fL --retry 3 -o "$BASE_IMG.part" "$url"; mv "$BASE_IMG.part" "$BASE_IMG"; }
  want=$(curl -fsSL "$url.sha256" | awk '{print $1}')
  echo "$want  $BASE_IMG" | sha256sum -c -
fi

for V in $VEHICLES; do
  [ -f "$HERE/config/$V.conf" ] || { echo "no profile for $V"; exit 1; }
  NAME="headunit-$V-$(date +%Y%m%d)"; IMG="$OUT/$NAME.img"
  echo "==> $NAME"
  case "$BASE_IMG" in *.xz) xz -dc "$BASE_IMG" > "$IMG";; *) cp "$BASE_IMG" "$IMG";; esac

  # Partition offsets from the image's table (works even where loop partition nodes don't appear, e.g. Docker).
  read -r s1 _ < <(sfdisk -d "$IMG" | awk -F'[=,]' '/start=/{print $2+0, $4+0; exit}')
  read -r s2 n2 < <(sfdisk -d "$IMG" | awk -F'[=,]' '/start=/{c++} c==2{print $2+0, $4+0; exit}')
  MNT=$(mktemp -d); STAGE=$(mktemp -d)
  ROOTLOOP=$(losetup -f --show -o $((s2*512)) --sizelimit $((n2*512)) "$IMG")
  cleanup() { sync; umount "$MNT" 2>/dev/null || true; losetup -d "$ROOTLOOP" 2>/dev/null || true; rmdir "$MNT" 2>/dev/null || true; rm -rf "$STAGE"; }
  trap cleanup EXIT
  mount "$ROOTLOOP" "$MNT"

  # 2) The package -> /opt/headunit (no node_modules, no build outputs)
  mkdir -p "$MNT/opt/headunit"
  rsync -a --delete --exclude 'tools/node_modules' --exclude 'out/' --exclude '.git' "$HERE/" "$MNT/opt/headunit/"
  chmod +x "$MNT"/opt/headunit/setup.sh "$MNT"/opt/headunit/bin/* "$MNT"/opt/headunit/setup.d/*.sh

  # 3) One-time first-boot service (enabled by symlink; runs only while headunit.conf exists)
  install -m 644 "$HERE/systemd/headunit-firstboot.service" "$MNT/etc/systemd/system/"
  ln -sf /etc/systemd/system/headunit-firstboot.service "$MNT/etc/systemd/system/multi-user.target.wants/headunit-firstboot.service"

  # 4) Editable settings on the boot partition (visible as "bootfs" on a Mac/PC)
  # (written with mtools, so the FAT boot partition never needs to be mounted)
  B="$STAGE/headunit"; mkdir -p "$B/hudiy"
  sed -e "s/^VEHICLE=.*/VEHICLE=$V/" -e "s/^TIER=.*/TIER=$TIER/" -e "s/^MAP_REGION=.*/MAP_REGION=$REGION/" "$HERE/config/headunit.conf.example" > "$B/headunit.conf"
  [ -n "$HUDIY" ] && cp "$HUDIY" "$B/hudiy/"
  cat > "$B/README-FIRST.txt" <<TXT
Head unit image for: $(grep VEHICLE_NAME "$HERE/config/$V.conf" | cut -d'"' -f2)
1. Edit headunit.conf in this folder if needed (tier, map region, Wi-Fi).
2. Put the Hudiy installer from your purchase email into the "hudiy" folder.
3. Eject, put the card in the Pi, power it on the bench with Wi-Fi in range.
   First boot takes 15-45 minutes (Hudiy, packages, map download) and reboots by itself.
   Progress log: this folder, firstboot.log
TXT
  mcopy -i "$IMG@@$((s1*512))" -s -o -Q "$B" ::/
  cleanup; trap - EXIT
  echo "compressing"; xz -T0 -6 -f "$IMG"
  (cd "$OUT" && sha256sum "$NAME.img.xz" > "$NAME.img.xz.sha256")
  echo "done: $OUT/$NAME.img.xz"
done
