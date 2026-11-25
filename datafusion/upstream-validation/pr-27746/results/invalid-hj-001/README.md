# Excluded official hj attempt

This archive preserves the first 90-slot official hj attempt. All three labelled arms registered SHA-256 `79e43db5fbc42debe10270027575c40102ece3707530b93d4776617274ae138f`. A compiled from its source tree. The B and C workspace builds reused A's executable from the shared Cargo target directory. The [registration](hj-official-sf10-001-registration.json) and [B](dfbench-B.log) and [C](dfbench-C.log) build logs show the same artifact despite different source checkouts.

All timings, pool peaks and process peaks from this attempt are excluded from A/B/C inference. The separate worker fingerprint campaign used independently verified binaries and is preserved with the accepted run's correctness evidence. The correction uses an individual Cargo target directory per arm, an executable-digest gate and a new interleaved registration. [Reproduction instructions](../../REPRODUCE.md).
