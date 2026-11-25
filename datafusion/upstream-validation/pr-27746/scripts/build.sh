#!/usr/bin/env bash
set -euo pipefail
umask 022
ulimit -c 0

package=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=${1:?usage: bash scripts/build.sh /absolute/scratch [A B C]}
shift
if (( $# == 0 )); then set -- A B; fi
repo="$root/upstream.git"
baseline=6a060586ed85d2ff71837b6d818b7eddc075b699
head=2f6c665fef63936f08a4aa7a4fb01b87a4d2b009
compiler=${VALIDATION_TOOLCHAIN:-1.97.0}
export CARGO_INCREMENTAL=0
mkdir -p "$root/bin" "$root/build"

for arm in "$@"; do
    case "$arm" in
        A) revision=$baseline ;;
        B|C) revision=$head ;;
        *) echo "Unknown arm: $arm" >&2; exit 2 ;;
    esac
    tree="$root/source-$arm"
    export CARGO_TARGET_DIR="$root/worker-target-$arm"
    if [[ ! -d "$tree" ]]; then
        git --git-dir="$repo" worktree add --detach "$tree" "$revision"
    fi
    [[ $(git -C "$tree" rev-parse HEAD) == "$revision" ]]
    if [[ $arm == C ]]; then
        candidate="$package/patches/review-4698342517.patch"
        if git -C "$tree" diff --quiet; then git -C "$tree" apply "$candidate"; fi
        git -C "$tree" apply --reverse --check "$candidate"
        git -C "$tree" -c core.abbrev=9 diff --binary | cmp "$candidate" -
        git -C "$tree" diff --binary > "$root/build/C.applied.patch"
    else
        [[ -z $(git -C "$tree" status --porcelain) ]]
    fi
    worker="$root/build/worker-$arm"
    mkdir -p "$worker/src"
    cp "$package/worker/src/main.rs" "$worker/src/"
    sed "s|@DF_ROOT@|$tree|g" "$package/worker/Cargo.toml.in" > "$worker/Cargo.toml"
    lock="$package/environment/worker-$arm.Cargo.lock"
    locked=()
    if [[ -f "$lock" ]]; then
        cp "$lock" "$worker/Cargo.lock"
        locked=(--locked)
    else
        cp "$tree/Cargo.lock" "$worker/Cargo.lock"
    fi
    cargo +"$compiler" build --manifest-path "$worker/Cargo.toml" --release --jobs "${VALIDATION_JOBS:-3}" "${locked[@]}"
    cp "$CARGO_TARGET_DIR/release/df-upstream-worker" "$root/bin/worker-$arm.new"
    mv "$root/bin/worker-$arm.new" "$root/bin/worker-$arm"
    {
        git -C "$tree" rev-parse HEAD HEAD^{tree}
        git -C "$tree" status --porcelain
        rustc +"$compiler" -vV
        cargo +"$compiler" -V
        sha256sum "$root/bin/worker-$arm" "$worker/Cargo.lock" "$worker/Cargo.toml" "$worker/src/main.rs"
        ldd "$root/bin/worker-$arm"
    } > "$root/build/$arm.provenance.txt"
done
