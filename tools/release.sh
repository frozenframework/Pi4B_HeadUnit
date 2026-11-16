#!/usr/bin/env bash
# Publish a head-unit update:   tools/release.sh 1.2.0 "What changed"
#
# Sets VERSION, commits it, creates a tag v1.2.0 signed with your release key, checks the signature
# the same way the Pi will, and pushes. Every head unit with Wi-Fi picks it up within ~30 minutes
# and installs it at its next key-on.
#
# The key's passphrase is asked for first, before anything changes (three tries). If a release stops part
# way - wrong passphrase, signing fails, Ctrl-C - everything it did is undone, so you can run the same
# command again with the same version number. A release left half-made by an older version of this
# script (a "Release X" commit with no tag, not on GitHub yet) is cleared away by itself too.
#
# One-time setup (on your Mac):
#   ssh-keygen -t ed25519 -C "headunit-release" -f ~/.ssh/headunit_release
#   ...and the .pub line must be in config/release-signers of the version the Pis are running.
set -euo pipefail
cd "$(dirname "$0")/.."
NEW=${1:-}; NOTES=${2:-"Head unit $NEW"}
KEY=${HU_RELEASE_KEY:-$HOME/.ssh/headunit_release}
[[ "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: tools/release.sh X.Y.Z \"what changed\""; exit 1; }

# Clear away releases that were started but never signed (no tag) and never reached GitHub: each is one
# commit that only changed VERSION. After this VERSION is the last real release again.
unfinished() {
  local v; v=$(cat VERSION)
  [ "$(git log -1 --format=%s)" = "Release $v" ] || return 1
  git rev-parse -q --verify "refs/tags/v$v" >/dev/null && return 1
  [ "$(git diff --name-only HEAD~1 HEAD)" = VERSION ] || return 1
  git rev-parse -q --verify '@{u}' >/dev/null 2>&1 && git merge-base --is-ancestor HEAD '@{u}' && return 1   # already pushed
  return 0
}
undo_release_commit() { git reset -q --mixed HEAD~1 && git checkout -q -- VERSION; }   # (docs/ edits are left alone)
while unfinished; do
  echo "clearing the unfinished release $(cat VERSION) (it was never signed or sent)"
  undo_release_commit
done
OLD=$(cat VERSION)
# The version file says OLD but it has no signed tag (its release commit already went to GitHub with a
# plain "git push"): the same number finishes it.
FINISH=0
if [ "$NEW" = "$OLD" ] && ! git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null && [ "$(git log -1 --format=%s)" = "Release $NEW" ]; then FINISH=1; fi
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
[ "$FINISH" = 1 ] || { [ "$NEW" != "$OLD" ] && [ "$newer" = "$NEW" ]; } || { echo "version $NEW must be higher than the current $OLD"; exit 1; }
[ -f "$KEY" ] && [ -f "$KEY.pub" ] || { echo "release key $KEY not found (see the top of this file)"; exit 1; }
grep -qF "$(cut -d' ' -f2 "$KEY.pub")" config/release-signers || {
  echo "your key isn't in config/release-signers, so the Pis would reject this release"; exit 1; }
if ! git diff --quiet -- . ':!docs' || ! git diff --cached --quiet; then
  echo "you have uncommitted changes (outside docs/). Commit them first, or they won't be in the release:"
  git status --short -- . ':!docs'; exit 1
fi

# The passphrase first, before anything changes: the key is unlocked into a private ssh-agent that lives
# only as long as this script, with three tries. Signing then uses that agent (no second prompt).
SIGNKEY=$KEY AGENT_PID="" ORIG_SOCK=${SSH_AUTH_SOCK:-}
if command -v ssh-agent >/dev/null && command -v ssh-add >/dev/null; then
  eval "$(ssh-agent -s)" >/dev/null; AGENT_PID=$SSH_AGENT_PID
  ok=0
  for try in 1 2 3; do
    if ssh-add -q "$KEY"; then ok=1; break; fi
    [ "$try" -lt 3 ] && echo "That passphrase didn't unlock the release key. Try again ($((3 - try)) left)."
  done
  if [ "$ok" != 1 ]; then
    kill "$AGENT_PID" 2>/dev/null || true
    echo "The release key wasn't unlocked, so nothing was changed. Run the same command again: tools/release.sh $NEW \"$NOTES\""
    echo "(To check the passphrase by itself: ssh-keygen -y -f $KEY)"
    exit 1
  fi
  SIGNKEY=$KEY.pub
fi
SIGN=(-c gpg.format=ssh -c "user.signingkey=$SIGNKEY")

# From here until the signed tag checks out, any failure (or Ctrl-C) puts everything back.
COMMITTED=0 DONE=0
cleanup() {
  [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true
  if [ "$DONE" != 1 ]; then
    git tag -d "v$NEW" >/dev/null 2>&1 || true
    if [ "$COMMITTED" = 1 ]; then undo_release_commit || true; fi
    echo; echo "The release wasn't made and nothing was sent; your files are as they were."
    echo "Run the same command again: tools/release.sh $NEW \"$NOTES\""
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM
if [ "$FINISH" != 1 ]; then
  echo "$NEW" > VERSION
  git add VERSION
  git commit -q -m "Release $NEW" -m "$NOTES"
  COMMITTED=1
fi
git "${SIGN[@]}" tag -s "v$NEW" -m "$NOTES"
# Check it exactly like the head unit will before anything leaves this Mac.
signers=$(mktemp); grep -v '^#' config/release-signers > "$signers"
git -c gpg.format=ssh -c "gpg.ssh.allowedSignersFile=$signers" verify-tag "v$NEW"; rm -f "$signers"
DONE=1                               # the release exists: a failed push is re-sent by running this again
if [ -n "$AGENT_PID" ]; then         # signing is done: the push uses your own SSH setup again, not the release key's agent
  kill "$AGENT_PID" 2>/dev/null || true; AGENT_PID=""
  if [ -n "$ORIG_SOCK" ]; then export SSH_AUTH_SOCK=$ORIG_SOCK; else unset SSH_AUTH_SOCK; fi
fi
git push origin HEAD "v$NEW" || { echo; echo "The push didn't go through (often a temporary GitHub error). Nothing is lost:"
  echo "run the same command again (tools/release.sh $NEW ...) and it sends the release that's ready."; exit 1; }
echo "released $NEW. Head units on Wi-Fi download it within ~30 min and install it at the next key-on."
