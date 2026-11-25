#!/usr/bin/env bash
set -euo pipefail
package=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
root=${1:?usage: bash scripts/build-memory.sh /absolute/scratch}
shift
if (( $# == 0 )); then set -- A B C; fi
export CARGO_INCREMENTAL=0
mkdir -p "$root/memory-bin"
for arm in "$@"; do
    export CARGO_TARGET_DIR="$root/worker-target-$arm"
    source="$root/source-$arm"
    worker="$root/build/memory-$arm"
    mkdir -p "$worker/src"
    python3 "$package/scripts/instrument_peak.py" "$package/worker/src/main.rs" > "$worker/src/main.rs"
    sed "s|@DF_ROOT@|$source|g" "$package/worker/Cargo.toml.in" > "$worker/Cargo.toml"
    cp "$root/build/worker-$arm/Cargo.lock" "$worker/Cargo.lock"
    cargo +1.97.0 build --locked --manifest-path "$worker/Cargo.toml" --release --jobs "${VALIDATION_JOBS:-3}"
    cp "$CARGO_TARGET_DIR/release/df-upstream-worker" "$root/memory-bin/worker-$arm"
    {
        git -C "$source" rev-parse HEAD HEAD^{tree}
        git -C "$source" status --porcelain
        sha256sum "$worker/src/main.rs" "$worker/Cargo.lock" "$root/memory-bin/worker-$arm"
    } > "$root/build/$arm.memory-provenance.txt"
done
