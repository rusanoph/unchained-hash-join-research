# DataFusion PR #27746 validation

| Recorded workload | Coverage | Sum of query medians A → B | Geomean A/B |
|---|---:|---:|---:|
| [Original SNAP](results/summaries/snap-001-A-B.json) | 35 | 26.266 → 6.814 s (−74.1%) | 2.714× |
| [SNAP holdout](results/summaries/snap-001-A-B.json) | 18 | 2.626 → 1.623 s (−38.2%) | 1.644× |
| [JOB](results/summaries/job-001-A-B.json) | 100 | 208.113 → 153.416 s (−26.3%) | **0.996×** |

JOB family 17 accounted for about 99% of the net time saved. JOB 7b slowed from 277.5 to 360.4 ms. Selective Slashdot two-hop and both Amazon out-stars showed smaller slowdowns. [Comparative report](reports/comparative-results.md) · [Raw SNAP](results/raw/snap-001.jsonl.gz) · [Raw JOB](results/raw/job-001.jsonl.gz) · [Reproduction](REPRODUCE.md).

The exact counting-sort patch reversed SF10 Q2's slowdown and improved Q4 and Q5 versus B by paired ratios of 1.57×, 1.41× and 1.43×. Q4 median peak pool reservation fell from 1,506.78 to 915.31 MiB. In the separate 64 MiB control, both sparse integer cases succeeded on A and exhausted the pool on B and C in all five repetitions. [x86 report](reports/counting-sort-x86.md) · [Raw hj](results/raw/hj-official-sf10-002.jsonl.gz) · [Controlled memory](reports/memory-analysis.md).

- A uses the merge-base on main, `6a060586ed85d2ff71837b6d818b7eddc075b699`.
- B uses PR head `2f6c665fef63936f08a4aa7a4fb01b87a4d2b009`.
- C uses B plus Matt Butrovich’s [exact review patch](patches/review-4698342517.patch).

Ryzen 5 7500F, WSL2, Rust 1.97.0, one logical CPU and one execution partition. Statement timing includes planning and full stream consumption. The original graphs and JOB use preloaded inputs. Holdout and official hj include Parquet reads. Operator build/probe times are wall-clock metrics. Process RSS, DataFusion pool reservations and whole-process CPU are recorded separately. Sampled build CPU was unavailable.

All 1,918 main observations succeeded with equal outputs and plan text. SNAP matched an independent count/sum oracle, JOB matched archived native fingerprints, and all six official hj cases matched independent multiset fingerprints. The separately registered 22d diagnostic has one pair. [Protocol](PROTOCOL.md) · [Source audit](analysis/architecture.md) · [Attribution and evidence transformations](ATTRIBUTION.md).

```sh
python3 scripts/verify_evidence.py
```

[GitHub Actions workflow](https://github.com/rusanoph/unchained-hash-join-research/blob/a29ad9a091f57171171923c99c334045afd171b6/.github/workflows/verify-upstream-validation.yml).
