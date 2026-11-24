# Unchained joins for Apache DataFusion

An independent research implementation of contiguous join indexes for
Apache DataFusion **55.2.0**. It includes a standalone Rust core, the
v10b integration for a pinned DataFusion revision, and a SQL test worker.
The design draws on the Unchained hash join paper.
[Simple, Efficient, and Robust Hash Tables for Join Processing](https://doi.org/10.1145/3662010.3663442)

The core stores matching build-side row IDs in contiguous ranges. It has an
exact-key layout for dense integer keys and a hash-based layout for general
keys. The DataFusion integration retains the native join in `native` mode,
supports forced conversion in `unchained` mode, and makes a build-time
decision in `auto` mode. The default mode is `native`.

In single-thread runs on the same engine build, `auto` at the 200,000-row
threshold reduced total query time relative to `native` by **73%** on
35 SNAP graph queries and **11.7%** on 100 JOB queries. Each total is the
sum of per-query median times. CSR ran in 24 SNAP queries and 56 JOB queries.
[Native versus auto results](docs/results.md#native-join-versus-auto-mode)

A follow-up lowered the `auto` threshold to 50,000 rows. It yielded a
**2.18×** speed ratio on held-out Enron and HepPh queries and **1.004×**
among the 31 analytical queries whose CSR builds changed. The default
`auto` threshold remains 200,000 rows.
[Threshold results](docs/results.md#lowering-the-build-threshold-in-auto-mode)

## Build and verify

Use Linux or WSL with Git, Python 3, a C toolchain, and rustup. The engine
build uses Rust 1.97.0 and the checked-in worker lockfile. Start in the
root of the combined repository.

```sh
cd datafusion
rustup toolchain install 1.97.0
cargo +1.97.0 test --manifest-path core/Cargo.toml
bash scripts/build-worker.sh
python3 -B scripts/smoke.py build/target/release/hashman-df-worker
```

The build fetches the pinned Apache DataFusion commit and prepares a
local build tree under `build/`. Set `DATAFUSION_SOURCE` to a local Git
checkout containing that commit to reuse local source. Pass a directory to
`build-worker.sh` to place the prepared sources and target files elsewhere.
After editing the source package, use a fresh build directory.

The synthetic smoke verifies the result digests against the native join.
It also checks that `csr_exact` and `csr_hash` actually execute and that
`auto` keeps the native index below the build-row threshold.

The [Enron example](docs/results.md#run-the-enron-two-hop-query) runs a
query from the graph holdout at both thresholds using the original SNAP
input and checks the exact result and the index built at each threshold.

## Source layout

| Path | Role |
|---|---|
| `core/` | Dependency-free CSR layouts and build gate with unit tests |
| `integrations/datafusion/` | DataFusion join adapter and telemetry |
| `scripts/patch_datafusion.py` | Exact-revision source preparation with upstream SHA256 verification |
| `worker/` | SQL execution worker and pinned Rust dependency resolution |
| `deps/` | DataFusion source pin and the measured v10b patch manifest |

## Provenance and licenses

The research implementation comes from `hashman-datafusion-lab` at
`acb1e92cddf1e1ff5d0b4adbd8f73e0a1bd24a9b`. The patch generator records
the hashes of the engine changes before adding distribution notices.
The historical manifest includes an older test fixture that relied on
release-mode wrapping. The fixture in this package wraps explicitly in
debug builds as well.
Apache DataFusion is fetched at the commit in [deps/datafusion.json](deps/datafusion.json).

The independent source is available under the [MIT license](LICENSE).
Upstream-derived portions in the patch generator are covered by the
[Apache DataFusion license](licenses/datafusion-LICENSE.txt) and
[NOTICE](NOTICE). Prepared upstream files retain their original notices
and identify the local modifications.
