#!/usr/bin/env bash
set -euo pipefail
root=${1:?usage: bash scripts/build-hj.sh /absolute/scratch}
shift
if (( $# == 0 )); then set -- A B C; fi
export CARGO_INCREMENTAL=0
mkdir -p "$root/hj-bin" "$root/hj-build"
for arm in "$@"; do
    case "$arm" in A|B|C) ;; *) exit 2 ;; esac
    (
        cd "$root/source-$arm"
        export CARGO_TARGET_DIR="$root/hj-target-$arm"
        git rev-parse HEAD HEAD^{tree}
        git status --porcelain
        printf 'Cargo target directory %s\n' "$CARGO_TARGET_DIR"
        rustc +1.97.0 -vV
        cargo +1.97.0 -V
        cargo +1.97.0 build --locked -p datafusion-benchmarks --bin dfbench \
            --profile release-nonlto --jobs "${VALIDATION_JOBS:-3}"
        cp "$CARGO_TARGET_DIR/release-nonlto/dfbench" "$root/hj-bin/dfbench-$arm.new"
        mv "$root/hj-bin/dfbench-$arm.new" "$root/hj-bin/dfbench-$arm"
        sha256sum "$root/hj-bin/dfbench-$arm" Cargo.lock
    ) > "$root/hj-build/$arm.log" 2>&1
done
