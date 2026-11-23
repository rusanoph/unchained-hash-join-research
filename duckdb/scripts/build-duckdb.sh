#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
build=$(realpath -m -- "${1:-$repo/build}")
cache=$(realpath -m -- "${UNCHAINED_CACHE:-$build/cache}")
jobs=${UNCHAINED_BUILD_JOBS:-2}
source_root="$build/source"

python3 -B "$repo/scripts/prepare_duckdb.py" "$build" "$cache"
cmake -S "$source_root" -B "$build/duckdb-build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_UNITTESTS=OFF -DBUILD_SHELL=ON \
    -DBUILD_EXTENSIONS='tpch;tpcds' -DEXTENSION_STATIC_BUILD=ON \
    -DOVERRIDE_GIT_DESCRIBE=v1.5.6-0-g069cc9f9b5 \
    -DENABLE_EXTENSION_AUTOLOADING=OFF -DENABLE_EXTENSION_AUTOINSTALL=OFF
cmake --build "$build/duckdb-build" --parallel "$jobs" --target duckdb src/libduckdb.so
"${CXX:-g++}" -std=c++11 -O3 -DNDEBUG -pthread "$repo/worker/main.cpp" \
    -I"$source_root/src/include" -I"$cache/json" -L"$build/duckdb-build/src" -lduckdb \
    -Wl,-rpath,'$ORIGIN/duckdb-build/src' -o "$build/hashman-duckdb"
printf 'Worker: %s\n' "$build/hashman-duckdb"
