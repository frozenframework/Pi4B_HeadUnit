#!/usr/bin/env bash
# Publish a head-unit update:   tools/release.sh 1.2.0 "What changed"
#
# Sets VERSION, commits it, creates a tag v1.2.0 signed with your release key, checks the signature
# the same way the Pi will, and pushes. Every head unit with Wi-Fi picks it up within ~30 minutes
# and installs it at its next key-on.
#
# One-time setup (on your Mac):
#   ssh-keygen -t ed25519 -C "headunit-release" -f ~/.ssh/headunit_release
#   ...and the .pub line must be in config/release-signers of the version the Pis are running.
set -euo pipefail
cd "$(dirname "$0")/.."
NEW=${1:-}; NOTES=${2:-"Head unit $NEW"}
KEY=${HU_RELEASE_KEY:-$HOME/.ssh/headunit_release}
[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: tools/release.sh X.Y.Z \"what changed\""; exit 1; }
OLD=$(cat VERSION)
# The same version again: if its release push didn't reach GitHub (e.g. "fatal error in commit_refs"),
# just send it now; nothing is made twice.
if [ "$NEW" = "$OLD" ] && git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null; then
  if git ls-remote --exit-code --tags origin "refs/tags/v$NEW" >/dev/null 2>&1; then
    echo "$NEW is already released (its tag is on GitHub). For new changes use a higher version."; exit 1
  fi
  echo "$NEW was made but didn't reach GitHub; sending it now"
  git push origin HEAD "v$NEW"
  echo "released $NEW. Head units on Wi-Fi download it within ~30 min and install it at the next key-on."; exit 0
fi
newer=$(printf '%s\n%s\n' "$OLD" "$NEW" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
[ "$NEW" != "$OLD" ] && [ "$newer" = "$NEW" ] || { echo "version $NEW must be higher than the current $OLD"; exit 1; }
[ -f "$KEY" ] && [ -f "$KEY.pub" ] || { echo "release key $KEY not found (see the top of this file)"; exit 1; }
grep -qF "$(cut -d' ' -f2 "$KEY.pub")" config/release-signers || {
  echo "your key isn't in config/release-signers, so the Pis would reject this release"; exit 1; }
if ! git diff --quiet -- . ':!docs' || ! git diff --cached --quiet; then
  echo "you have uncommitted changes (outside docs/). Commit them first, or they won't be in the release:"
  git status --short -- . ':!docs'; exit 1
fi
SIGN=(-c gpg.format=ssh -c "user.signingkey=$KEY")     # asks for the key's passphrase
echo "$NEW" > VERSION
git add VERSION
git commit -q -m "Release $NEW" -m "$NOTES"
git "${SIGN[@]}" tag -s "v$NEW" -m "$NOTES"
# Check it exactly like the head unit will before anything leaves this Mac.
signers=$(mktemp); grep -v '^#' config/release-signers > "$signers"
git -c gpg.format=ssh -c "gpg.ssh.allowedSignersFile=$signers" verify-tag "v$NEW"; rm -f "$signers"
git push origin HEAD "v$NEW" || { echo; echo "The push didn't go through (often a temporary GitHub error). Nothing is lost:"
  echo "run the same command again (tools/release.sh $NEW ...) and it sends the release that's ready."; exit 1; }
echo "released $NEW. Head units on Wi-Fi download it within ~30 min and install it at the next key-on."
