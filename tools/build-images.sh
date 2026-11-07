#!/usr/bin/env bash
# Build the flash-ready images for all three vehicles from one release, on the Mac (no Docker, no root):
#
#   tools/build-images.sh 1.2.29                                   public images (no Hudiy inside)
#   tools/build-images.sh 1.2.29 --hudiy ~/Downloads/hudiy_installer.tar.gz
#                                                                  public AND private ("-with-hudiy") images
#
# Each image is built from the signed release tag v<version> (not whatever is in the folder), set up for
# its vehicle at tier 3 (Miata and F-250: CarPiHAT, radio, whole-US maps; Frontier: its tier 3 - 1024x600
# screen, steering-wheel controls, backup camera, Texas + neighbours maps). Your boot image
# (private/boot-splash.png) goes in when it's there.
#   out/headunit-<vehicle>-<version>.img.xz             public: fine to share / attach to the GitHub release
#   out/headunit-<vehicle>-<version>-with-hudiy.img.xz  PRIVATE: has your licensed Hudiy installer - keep it
#                                                       to yourself, never upload or commit it
# The Raspberry Pi OS download is kept in out/cache, so later builds skip it. About a minute per image: only
# the part of the official image holding the boot partition is redone (image/xz_splice.py).
set -euo pipefail
cd "$(dirname "$0")/.."
V=${1:?usage: tools/build-images.sh X.Y.Z [--hudiy installer] [--tier N] [--vehicles "miata f250 frontier"]}; shift
HUDIY="" TIER=3 VEHICLES="miata f250 frontier" EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --hudiy) HUDIY=$2; shift 2;;
    --tier) TIER=$2; shift 2;;
    --vehicles) VEHICLES=$2; shift 2;;
    *) EXTRA+=("$1"); shift;;
  esac
done
git rev-parse -q --verify "refs/tags/v$V" >/dev/null || { echo "No release v$V yet: run tools/release.sh $V \"...\" first."; exit 1; }
python3 -c "import pyfatfs" 2>/dev/null || { echo "Installing pyfatfs (needed to write the card's boot partition)..."; python3 -m pip install --user -q pyfatfs; }
SPLASH=(); [ -f private/boot-splash.png ] && SPLASH=(--splash private/boot-splash.png)
mkdir -p out
for v in $VEHICLES; do
  python3 image/make-image.py --vehicle "$v" --tier "$TIER" --release "$V" ${SPLASH[@]+"${SPLASH[@]}"} ${EXTRA[@]+"${EXTRA[@]}"}
  if [ -n "$HUDIY" ]; then
    python3 image/make-image.py --vehicle "$v" --tier "$TIER" --release "$V" --hudiy "$HUDIY" ${SPLASH[@]+"${SPLASH[@]}"} ${EXTRA[@]+"${EXTRA[@]}"}
  fi
done
echo
echo "Images for $V in out/:"
ls -lh out/headunit-*-"$V"*.img.xz
if [ -n "$HUDIY" ]; then echo "(the -with-hudiy ones contain your Hudiy installer: keep them private)"; fi
