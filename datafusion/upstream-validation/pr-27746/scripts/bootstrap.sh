#!/usr/bin/env bash
set -euo pipefail
root=${1:?usage: bash scripts/bootstrap.sh /absolute/scratch}
repo="$root/upstream.git"
origin=https://github.com/apache/datafusion.git
baseline=6a060586ed85d2ff71837b6d818b7eddc075b699
head=2f6c665fef63936f08a4aa7a4fb01b87a4d2b009
observed_main=73043acbe3fbe681a0219c2060eb4eb6e9d47228
mkdir -p "$root"
if [[ ! -d "$repo" ]]; then git init --bare "$repo"; fi
git --git-dir="$repo" fetch --no-tags --depth=40 "$origin" \
    "+$head:refs/heads/pinned-pr" "+$observed_main:refs/heads/upstream-main"
test "$(git --git-dir="$repo" merge-base pinned-pr upstream-main)" = "$baseline"
git --git-dir="$repo" log --oneline "$baseline"..pinned-pr
