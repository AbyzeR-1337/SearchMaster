#!/usr/bin/env bash
# Publish a finished search to the results branch so the report can be read on
# GitHub (also from a phone). Only report files are committed; downloaded files
# (screenshots, documents, code archives) stay in the run's artifact.
#
# usage: publish.sh <out_dir> <query>
set -euo pipefail

OUT_DIR="$1"
QUERY="${2:-search}"
BRANCH="${SM_RESULTS_BRANCH:-search-results}"
WORK="$(mktemp -d)"
TMP_BRANCH="sm-publish-$$-${RANDOM}"

git config --global user.name "github-actions[bot]"
git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"

cleanup() {
  git worktree remove --force "$WORK" >/dev/null 2>&1 || rm -rf "$WORK"
  git worktree prune
  git branch -D "$TMP_BRANCH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

prepare() {
  cleanup
  if git fetch --depth 1 origin "$BRANCH" 2>/dev/null; then
    git worktree add -f --detach "$WORK" FETCH_HEAD
  else
    git worktree add -f --orphan -b "$TMP_BRANCH" "$WORK"
  fi
  mkdir -p "$WORK/$OUT_DIR"
  # everything except the heavy downloads in files/
  find "$OUT_DIR" -maxdepth 1 -type f -exec cp {} "$WORK/$OUT_DIR/" \;
  python -m searchmaster.gh index "$WORK"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "🔍 ${QUERY}" || true
}

for attempt in 1 2 3 4 5; do
  prepare
  if git -C "$WORK" push origin "HEAD:refs/heads/$BRANCH"; then
    echo "Published to branch $BRANCH: $OUT_DIR"
    exit 0
  fi
  echo "Push failed (attempt $attempt), retrying…"
  sleep $((attempt * 3))
done
echo "::warning::Could not publish results to branch $BRANCH"
exit 1
