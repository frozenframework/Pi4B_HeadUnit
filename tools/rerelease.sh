#!/usr/bin/env bash
# Send every head unit back to an earlier release's software:   tools/rerelease.sh 1.2.7 1.2.9
#
# Head units only ever install a *newer* version number, so this publishes the files of v1.2.7 as a
# new version (1.2.9 here). It makes one commit that sets every file (except docs/) back to how it was
# in v1.2.7, then runs tools/release.sh to sign and push it. Nothing is lost: the later work stays in
# git history, and you can bring it back later with a normal release.
# (On a single head unit you can also go back from Settings > Updates > Go back.)
set -euo pipefail
cd "$(dirname "$0")/.."
OLD=${1:-}; NEW=${2:-}
[[ "$OLD" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: tools/rerelease.sh OLD NEW   (e.g. 1.2.7 1.2.9)"; exit 1; }
git rev-parse -q --verify "refs/tags/v$OLD" >/dev/null || { echo "there's no release v$OLD"; exit 1; }
if ! git diff --quiet -- . ':!docs' || ! git diff --cached --quiet; then
  echo "commit or put away your changes (outside docs/) first:"; git status --short -- . ':!docs'; exit 1
fi
git restore --source="v$OLD" --staged --worktree -- . ':(exclude)docs'
git commit -q -m "Go back to the software of $OLD" -m "Same files as v$OLD (docs/ unchanged); published as $NEW so head units install it."
[ -x tools/release.sh ] || { echo "v$OLD has no tools/release.sh; release by hand"; exit 1; }
exec tools/release.sh "$NEW" "Same software as $OLD"
