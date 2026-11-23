#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
build=$(realpath -m -- "${1:-$root/build}")
source_args=()
if [[ -n "${DATAFUSION_SOURCE:-}" ]]; then
    source_args=(--source "$DATAFUSION_SOURCE")
fi
python3 -B "$root/scripts/prepare_worker.py" "$build" "${source_args[@]}"
target=$(realpath -m -- "${CARGO_TARGET_DIR:-$build/target}")
CARGO_TARGET_DIR="$target" cargo +1.97.0 build --release --locked --features hashman \
    --manifest-path "$build/prepared/worker/Cargo.toml"
printf 'Worker: %s\n' "$target/release/hashman-df-worker"
