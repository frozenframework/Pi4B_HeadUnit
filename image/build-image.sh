#!/usr/bin/env bash
# =============================================================================================
#  Build ready-to-flash head-unit images in a Docker container (for computers without Python 3).
#  It runs image/make-image.py inside debian:trixie, so both ways produce identical images.
#
#      ./image/build-image.sh --vehicle miata [--tier 2] [--base out/cache/<raspios>.img.xz] [--hudiy <installer>]
#
#  With Python 3 on your computer you don't need Docker:
#      python3 -m pip install pyfatfs && python3 image/make-image.py --vehicle miata --base ...
# =============================================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
args=() mounts=()
while [ $# -gt 0 ]; do
  case "$1" in
    --hudiy|--base)        # files outside the project are mounted read-only into the container
      f=$(cd "$(dirname "$2")" && pwd)/$(basename "$2"); mounts+=(-v "$f:/in/$(basename "$2"):ro")
      args+=("$1" "/in/$(basename "$2")"); shift 2;;
    *) args+=("$1"); shift;;
  esac
done
command -v docker >/dev/null || { echo "Needs Docker Desktop, or run: python3 image/make-image.py ${args[*]}"; exit 1; }
exec docker run --rm -v "$HERE":/work "${mounts[@]}" -w /work debian:trixie bash -c \
  'apt-get update -qq && apt-get install -y -qq python3-pip xz-utils ca-certificates >/dev/null &&
   pip3 install -q --break-system-packages pyfatfs && python3 image/make-image.py "$@"' make-image "${args[@]}"
