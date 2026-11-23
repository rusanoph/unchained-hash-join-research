// hashman-datafusion-lab integration. This file is copied into the patched DataFusion tree as
// datafusion/physical-plan/src/joins/hashman.rs by scripts/patch_datafusion.py. Edit it in the lab, never in the tree.
//
//! Contiguous (Unchained-style) build-side index for `HashJoinExec`, settings and telemetry.
//!
//! Settings (datafusion.execution.*): `hashman_join` = native | unchained | auto, `hashman_join_min_build_rows`,
//! `hashman_join_min_multiplicity`, `hashman_join_max_locality`, `hashman_join_profile`.
//!
//! * `native`: every code path of the stock engine is untouched.
//! * `unchained`: every supported build side gets the contiguous index (no stock index is built first).
//! * `auto`: the decision is taken BEFORE anything is built, from a small sample (about 25K rows): the build
//!   side must have at least `min_build_rows` rows, an effective key multiplicity of at least `min_multiplicity` and a
//!   key locality below `max_locality` (key-clustered builds are already cache friendly under the stock chain).
//!   A rejected join runs the stock code unchanged after the sample.

use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::time::Instant;

use arrow::array::{Array, ArrayRef, BooleanArray, UInt32Array};
use arrow::buffer::{BooleanBuffer, NullBuffer};
use arrow::compute::take_record_batch;
use arrow::record_batch::RecordBatch;
use datafusion_common::config::ConfigOptions;
use datafusion_common::hash_utils::{RandomState, create_hashes};
use datafusion_common::{NullEquality, Result, internal_datafusion_err, plan_err};
use datafusion_execution::memory_pool::MemoryReservation;
use datafusion_physical_expr::PhysicalExprRef;
use datafusion_physical_expr_common::utils::evaluate_expressions_to_arrays;
use hashman_join_core::HashCsr;
use hashman_join_core::gate::{self, Estimate};

use crate::joins::array_map::ArrayMap;
use crate::joins::join_hash_map::JoinHashMapType;
use crate::joins::utils::{BuildProbeJoinMetrics, matchable_join_keys};
use crate::joins::{Map, MapOffset};

pub const SCHEMA_VERSION: u32 = 6;

/// `reorder` (physical payload permutation) pays only for big, unordered, multi-row builds (grid 003): in `auto` mode it is applied
/// when the build has at least this many rows, an effective multiplicity of at least `REORDER_MIN_MULT` and a locality below
/// `REORDER_MAX_LOCALITY`. In `unchained` mode the setting alone decides.
const REORDER_MIN_ROWS: usize = 1 << 20;
const REORDER_MIN_MULT: f64 = 4.0;
const REORDER_MAX_LOCALITY: f64 = 0.25;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Native,
    Unchained,
    Auto,
}

#[derive(Clone, Debug)]
pub struct HashmanParams {
    pub mode: Mode,
    pub min_build_rows: usize,
    pub min_multiplicity: f64,
    pub max_locality: f64,
    pub reorder: bool,
    pub prefetch: bool,
    /// `auto` keeps the stock index when the estimated number of probe rows is below `min_probe_ratio x build rows`
    /// (the conversion costs more to build; a small probe side cannot amortise it). 0 disables the check.
    pub min_probe_ratio: f64,
    /// Optimizer estimate of the probe-side rows of this build (set by `HashJoinExec::execute`), if known.
    pub probe_rows: Option<usize>,
    pub profile: bool,
}

/// Outcome of the build-time decision, kept for telemetry.
#[derive(Clone, Debug)]
pub struct Decision {
    pub convert: bool,
    pub reorder_ok: bool,
    pub reason: &'static str,
    pub est_multiplicity: f64,
    pub locality: f64,
    pub gate_ns: u64,
    /// estimated probe rows (-1: unknown)
    pub probe_rows: i64,
}

impl Decision {
    fn no(reason: &'static str) -> Self {
        Self { convert: false, reorder_ok: false, reason, est_multiplicity: 0.0, locality: 0.0, gate_ns: 0, probe_rows: -1 }
    }
}

impl HashmanParams {
    pub fn from_config(config: &ConfigOptions) -> Result<Self> {
        let e = &config.execution;
        let mode = match e.hashman_join.as_str() {
            "native" => Mode::Native,
            "unchained" => Mode::Unchained,
            "auto" => Mode::Auto,
            other => {
                return plan_err!(
                    "datafusion.execution.hashman_join must be native, unchained or auto, got {other}"
                );
            }
        };
        PROFILE.store(e.hashman_join_profile, Ordering::Relaxed);
        Ok(Self {
            mode,
            min_build_rows: e.hashman_join_min_build_rows,
            min_multiplicity: e.hashman_join_min_multiplicity,
            max_locality: e.hashman_join_max_locality,
            reorder: e.hashman_join_reorder,
            prefetch: e.hashman_join_prefetch,
            min_probe_ratio: e.hashman_join_min_probe_ratio,
            probe_rows: None,
            profile: e.hashman_join_profile,
        })
    }

    fn decide(&self, rows: usize, sample: impl FnOnce() -> Result<Estimate>) -> Result<Decision> {
        match self.mode {
            Mode::Native => Ok(Decision::no("native_mode")),
            Mode::Unchained => Ok(Decision {
                convert: true,
                reorder_ok: self.reorder,
                reason: "forced",
                probe_rows: self.probe_rows.map_or(-1, |p| p as i64),
                ..Decision::no("")
            }),
            Mode::Auto => {
                if rows < self.min_build_rows {
                    return Ok(Decision::no("below_min_rows"));
                }
                let probe_rows = self.probe_rows.map_or(-1, |p| p as i64);
                if self.min_probe_ratio > 0.0 && self.probe_rows.is_some_and(|p| (p as f64) < self.min_probe_ratio * rows as f64) {
                    return Ok(Decision { probe_rows, ..Decision::no("small_probe") });
                }
                let t = Instant::now();
                let est = sample()?;
                let gate_ns = t.elapsed().as_nanos() as u64;
                let (convert, reason) = if est.multiplicity < self.min_multiplicity {
                    (false, "low_multiplicity")
                } else if est.locality >= self.max_locality {
                    (false, "ordered_build")
                } else {
                    (true, "gate_passed")
                };
                let reorder_ok = convert
                    && self.reorder
                    && rows >= REORDER_MIN_ROWS
                    && est.multiplicity >= REORDER_MIN_MULT
                    && est.locality < REORDER_MAX_LOCALITY;
                Ok(Decision { convert, reorder_ok, reason, est_multiplicity: est.multiplicity, locality: est.locality, gate_ns, probe_rows })
            }
        }
    }
}

// ---------------------------------------------------------------- telemetry

/// 0 stock hashbrown+chain, 1 stock ArrayMap, 2 hash CSR, 3 exact CSR
pub const KIND_NAMES: [&str; 4] = ["native_hash", "native_array", "csr_hash", "csr_exact"];

static PROFILE: AtomicBool = AtomicBool::new(false);
static LOOKUP_NS: [AtomicU64; 4] = [AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0)];
static LOOKUP_CALLS: [AtomicU64; 4] = [AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0)];
static LOOKUP_PAIRS: [AtomicU64; 4] = [AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0), AtomicU64::new(0)];
static BUILDS: Mutex<Vec<BuildRecord>> = Mutex::new(Vec::new());
static REORDER_NS: AtomicU64 = AtomicU64::new(0);
static REORDER_ROWS: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug)]
pub struct BuildRecord {
    pub kind: usize,
    pub decision: Decision,
    pub build_rows: usize,
    pub distinct: usize,
    pub map_bytes: usize,
    /// whole map construction in `collect_left_input` (gate sample, hashing, index, concat)
    pub build_ns: u64,
}

pub fn map_kind(map: &Map) -> usize {
    match map {
        Map::HashMap(m) => m.hashman_kind() as usize,
        Map::ArrayMap(a) => a.hashman_kind(),
    }
}

fn push_record(kind: usize, decision: &Decision, rows: usize, distinct: usize, bytes: usize, t0: Instant) {
    BUILDS.lock().unwrap().push(BuildRecord {
        kind,
        decision: decision.clone(),
        build_rows: rows,
        distinct,
        map_bytes: bytes,
        build_ns: t0.elapsed().as_nanos() as u64,
    });
}

#[inline(always)]
pub fn probe_start() -> Option<Instant> {
    if PROFILE.load(Ordering::Relaxed) { Some(Instant::now()) } else { None }
}

#[inline(always)]
pub fn probe_end(t: Option<Instant>, map: &Map, pairs: usize) {
    if let Some(t) = t {
        let k = map_kind(map);
        LOOKUP_NS[k].fetch_add(t.elapsed().as_nanos() as u64, Ordering::Relaxed);
        LOOKUP_CALLS[k].fetch_add(1, Ordering::Relaxed);
        LOOKUP_PAIRS[k].fetch_add(pairs as u64, Ordering::Relaxed);
    }
}

/// JSON report of everything recorded since the previous call; resets the counters.
pub fn take_report() -> String {
    let builds = std::mem::take(&mut *BUILDS.lock().unwrap());
    let mut s = format!("{{\"schema_version\":{SCHEMA_VERSION},\"builds\":[");
    for (i, b) in builds.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        let d = &b.decision;
        s.push_str(&format!(
            "{{\"kind\":\"{}\",\"reason\":\"{}\",\"build_rows\":{},\"distinct\":{},\"map_bytes\":{},\"build_ns\":{},\"est_multiplicity\":{:.3},\"locality\":{:.4},\"gate_ns\":{},\"est_probe_rows\":{}}}",
            KIND_NAMES[b.kind], d.reason, b.build_rows, b.distinct, b.map_bytes, b.build_ns, d.est_multiplicity, d.locality, d.gate_ns, d.probe_rows
        ));
    }
    s.push_str("],\"lookup\":{");
    for k in 0..4 {
        if k > 0 {
            s.push(',');
        }
        s.push_str(&format!(
            "\"{}\":{{\"ns\":{},\"calls\":{},\"pairs\":{}}}",
            KIND_NAMES[k],
            LOOKUP_NS[k].swap(0, Ordering::Relaxed),
            LOOKUP_CALLS[k].swap(0, Ordering::Relaxed),
            LOOKUP_PAIRS[k].swap(0, Ordering::Relaxed)
        ));
    }
    s.push_str(&format!(
        "}},\"reorder\":{{\"ns\":{},\"rows\":{}}}}}",
        REORDER_NS.swap(0, Ordering::Relaxed),
        REORDER_ROWS.swap(0, Ordering::Relaxed)
    ));
    s
}

// ---------------------------------------------------------------- hash CSR as a JoinHashMapType

#[derive(Debug)]
pub struct UnchainedJoinHashMap {
    csr: HashCsr,
    distinct: usize,
    prefetch: bool,
    reorder_ok: bool,
}

impl UnchainedJoinHashMap {
    /// `segments`: start offsets of the batches in `hashes` (see `HashCsr::build_segmented`).
    pub fn from_hashes(
        hashes: &[u64],
        valid: Option<&[bool]>,
        known_distinct: Option<usize>,
        segments: &[usize],
        prefetch: bool,
        reorder_ok: bool,
    ) -> Result<Self> {
        let csr = match valid {
            None => HashCsr::build_segmented(hashes, |_| true, true, segments),
            Some(v) => HashCsr::build_segmented(hashes, |i| v[i], true, segments),
        }
        .map_err(|e| internal_datafusion_err!("hashman CSR build failed: {e}"))?;
        let distinct = known_distinct.unwrap_or_else(|| csr.count_distinct());
        Ok(Self { csr, distinct, prefetch, reorder_ok })
    }
}

/// Appends the candidates of `entries` whose 32-bit hash filter equals that of `h`; returns how many were appended.
/// Branch-free compaction: every candidate is written, the output length advances only for matches.
#[inline(always)]
fn emit_matches(entries: &[u64], h: u64, probe_idx: u32, pi: &mut Vec<u32>, bi: &mut Vec<u64>) -> usize {
    let want = h & 0xFFFF_FFFF;
    pi.reserve(entries.len());
    bi.reserve(entries.len());
    let len0 = bi.len();
    debug_assert_eq!(pi.len(), len0);
    let mut k = 0usize;
    // SAFETY: both vectors have at least `entries.len()` spare slots (reserved above); only the first `k` written
    // slots are exposed through set_len.
    unsafe {
        let pp = pi.as_mut_ptr().add(len0);
        let bp = bi.as_mut_ptr().add(len0);
        for &e in entries {
            *bp.add(k) = e & 0xFFFF_FFFF;
            *pp.add(k) = probe_idx;
            k += ((e >> 32) == want) as usize;
        }
        pi.set_len(len0 + k);
        bi.set_len(len0 + k);
    }
    k
}

impl UnchainedJoinHashMap {
    #[inline(always)]
    fn lookup<const PF: bool>(
        &self,
        hash_values: &[u64],
        valid_keys: Option<&NullBuffer>,
        limit: usize,
        offset: MapOffset,
        input_indices: &mut Vec<u32>,
        match_indices: &mut Vec<u64>,
    ) -> Option<MapOffset> {
        input_indices.clear();
        match_indices.clear();
        let n = hash_values.len();
        let entries = self.csr.entries();
        let mut remaining = limit;
        // resume protocol: (idx, None) start at idx; (idx, Some(0)) idx is finished; (idx, Some(p + 1)) continue idx
        // at entry position p
        let (mut idx, mut pending) = match offset {
            (i, None) => (i, None),
            (i, Some(0)) => (i + 1, None),
            (i, Some(p1)) => (i, Some(p1 as usize - 1)),
        };
        while idx < n {
            if PF {
                if idx + 16 < n {
                    self.csr.prefetch_slot(hash_values[idx + 16]);
                }
                if idx + 8 < n {
                    self.csr.prefetch_run(hash_values[idx + 8]);
                }
            }
            if !valid_keys.is_some_and(|v| v.is_null(idx)) {
                let h = hash_values[idx];
                let (a, b) = self.csr.run(h);
                let mut pos = pending.take().unwrap_or(a);
                while pos < b {
                    // examine at most `remaining` candidates: the limit holds without a per-pair check
                    let chunk = (b - pos).min(remaining);
                    let emitted = emit_matches(&entries[pos..pos + chunk], h, idx as u32, input_indices, match_indices);
                    pos += chunk;
                    remaining -= emitted;
                    if remaining == 0 {
                        return if pos >= b {
                            if idx == n - 1 { None } else { Some((idx, Some(0))) }
                        } else {
                            Some((idx, Some(pos as u64 + 1)))
                        };
                    }
                }
            }
            idx += 1;
        }
        None
    }
}

impl JoinHashMapType for UnchainedJoinHashMap {
    fn extend_zero(&mut self, _: usize) {}

    fn update_from_iter<'a>(
        &mut self,
        _iter: Box<dyn Iterator<Item = (usize, &'a u64)> + Send + 'a>,
        _deleted_offset: usize,
    ) {
        unreachable!("UnchainedJoinHashMap is immutable and is only built by hashman::try_hash_csr")
    }

    fn get_matched_indices<'a>(
        &self,
        iter: Box<dyn Iterator<Item = (usize, &'a u64)> + 'a>,
        deleted_offset: Option<usize>,
    ) -> (Vec<u32>, Vec<u64>) {
        assert!(deleted_offset.is_none(), "pruning joins do not use UnchainedJoinHashMap");
        let entries = self.csr.entries();
        let (mut input, mut matched) = (vec![], vec![]);
        for (row_idx, &h) in iter {
            let (a, b) = self.csr.run(h);
            for &e in &entries[a..b] {
                if HashCsr::entry_matches(e, h) {
                    input.push(row_idx as u32);
                    matched.push(HashCsr::entry_row(e) as u64);
                }
            }
        }
        (input, matched)
    }

    fn get_matched_indices_with_limit_offset(
        &self,
        hash_values: &[u64],
        valid_keys: Option<&NullBuffer>,
        limit: usize,
        offset: MapOffset,
        input_indices: &mut Vec<u32>,
        match_indices: &mut Vec<u64>,
    ) -> Option<MapOffset> {
        if self.prefetch {
            self.lookup::<true>(hash_values, valid_keys, limit, offset, input_indices, match_indices)
        } else {
            self.lookup::<false>(hash_values, valid_keys, limit, offset, input_indices, match_indices)
        }
    }

    fn contain_hashes(&self, hash_values: &[u64]) -> BooleanArray {
        let buffer = BooleanBuffer::collect_bool(hash_values.len(), |i| self.csr.contains(hash_values[i]));
        BooleanArray::new(buffer, None)
    }

    fn is_empty(&self) -> bool {
        self.csr.is_empty()
    }

    fn len(&self) -> usize {
        self.distinct
    }

    fn hashman_kind(&self) -> u8 {
        2
    }

    fn hashman_take_permutation(&mut self, total_rows: usize) -> Option<Vec<u32>> {
        if self.reorder_ok { Some(self.csr.take_permutation(total_rows)) } else { None }
    }

    fn hashman_bytes(&self) -> usize {
        self.csr.bytes()
    }
}

/// Optimizer estimate of the rows the probe side will deliver (`None` when unknown).
pub fn probe_rows_estimate(right: &std::sync::Arc<dyn crate::ExecutionPlan>, partition: Option<usize>) -> Option<usize> {
    // `ExecutionPlan::partition_statistics` is deprecated and reports "unknown" by default since 55.0; the statistics walk is the API
    use crate::statistics::{StatisticsArgs, StatisticsContext};
    StatisticsContext::new()
        .compute(right.as_ref(), &StatisticsArgs::new().with_partition(partition))
        .ok()
        .and_then(|s| s.num_rows.get_value().copied())
}

// ---------------------------------------------------------------- decision samples

fn append_hashes(
    batch: &RecordBatch,
    on_left: &[PhysicalExprRef],
    random_state: &RandomState,
    null_equality: NullEquality,
    out: &mut Vec<u64>,
) -> Result<()> {
    let keys = evaluate_expressions_to_arrays(on_left, batch)?;
    let mut buf = vec![0u64; batch.num_rows()];
    create_hashes(&keys, random_state, &mut buf)?;
    match matchable_join_keys(&keys, null_equality) {
        None => out.extend_from_slice(&buf),
        Some(valid) => out.extend(buf.iter().enumerate().filter(|(i, _)| valid.is_valid(*i)).map(|(_, h)| *h)),
    }
    Ok(())
}

/// Hashes of a random sample of build rows and of a few windows of consecutive rows, computed exactly like the build.
fn sample_hashes(
    batches: &[RecordBatch],
    on_left: &[PhysicalExprRef],
    random_state: &RandomState,
    null_equality: NullEquality,
    n: usize,
) -> Result<Estimate> {
    let plan = gate::plan_sample(n);
    let mut starts = Vec::with_capacity(batches.len());
    let mut acc = 0usize;
    for b in batches {
        starts.push(acc);
        acc += b.num_rows();
    }
    let locate = |pos: usize| starts.partition_point(|&s| s <= pos) - 1;
    let mut random_vals = Vec::with_capacity(plan.random.len());
    let mut i = 0;
    while i < plan.random.len() {
        let bi = locate(plan.random[i]);
        let end = starts[bi] + batches[bi].num_rows();
        let mut idx: Vec<u32> = Vec::new();
        while i < plan.random.len() && plan.random[i] < end {
            idx.push((plan.random[i] - starts[bi]) as u32);
            i += 1;
        }
        let sub = take_record_batch(&batches[bi], &UInt32Array::from(idx))?;
        append_hashes(&sub, on_left, random_state, null_equality, &mut random_vals)?;
    }
    let mut windows = Vec::with_capacity(plan.windows.len());
    for &(start, len) in &plan.windows {
        let bi = locate(start);
        let off = start - starts[bi];
        let len = len.min(batches[bi].num_rows() - off);
        let mut v = Vec::with_capacity(len);
        append_hashes(&batches[bi].slice(off, len), on_left, random_state, null_equality, &mut v)?;
        windows.push(v);
    }
    Ok(gate::estimate(n, &random_vals, &windows))
}

fn sample_array(array: &ArrayRef) -> Result<Estimate> {
    let n = array.len();
    let plan = gate::plan_sample(n);
    let random_vals: Vec<u64> = ArrayMap::hashman_keys_at(array, &plan.random)?.into_iter().flatten().collect();
    let mut windows = Vec::with_capacity(plan.windows.len());
    let mut positions: Vec<usize> = Vec::new();
    for &(start, len) in &plan.windows {
        positions.clear();
        positions.extend(start..start + len);
        windows.push(ArrayMap::hashman_keys_at(array, &positions)?.into_iter().flatten().collect());
    }
    Ok(gate::estimate(n, &random_vals, &windows))
}

// ---------------------------------------------------------------- build entry points

/// ArrayMap side: decide before the stock `ArrayMap::try_new` is run.
pub fn decide_array(params: &HashmanParams, array: &ArrayRef, num_rows: usize) -> Result<Decision> {
    params.decide(num_rows, || sample_array(array))
}

pub fn record_array_build(array_map: &ArrayMap, decision: &Decision, num_rows: usize, t0: Instant) {
    push_record(array_map.hashman_kind(), decision, num_rows, array_map.num_of_distinct_key(), array_map.size(), t0);
}

/// Hashes the whole build side exactly like `update_hash` (same keys, same seed, same row order: batches in reverse)
/// and indexes it. Rows whose key can never match are not indexed.
fn build_unchained_hash(
    batches: &[RecordBatch],
    on_left: &[PhysicalExprRef],
    random_state: &RandomState,
    null_equality: NullEquality,
    num_rows: usize,
    prefetch: bool,
    reorder_ok: bool,
    reservation: &mut MemoryReservation,
) -> Result<UnchainedJoinHashMap> {
    // transient: one hash per build row
    let transient = num_rows.saturating_mul(8);
    reservation.try_grow(transient)?;
    let mut hashes: Vec<u64> = Vec::with_capacity(num_rows);
    let mut valid: Option<Vec<bool>> = None;
    let mut buf: Vec<u64> = Vec::new();
    let mut segments: Vec<usize> = Vec::with_capacity(batches.len());
    for batch in batches.iter().rev() {
        segments.push(hashes.len());
        let keys = evaluate_expressions_to_arrays(on_left, batch)?;
        buf.clear();
        buf.resize(batch.num_rows(), 0);
        create_hashes(&keys, random_state, &mut buf)?;
        let before = hashes.len();
        hashes.extend_from_slice(&buf);
        match matchable_join_keys(&keys, null_equality) {
            Some(nulls) => {
                let v = valid.get_or_insert_with(|| vec![true; before]);
                v.extend((0..batch.num_rows()).map(|i| nulls.is_valid(i)));
            }
            None => {
                if let Some(v) = valid.as_mut() {
                    v.extend(std::iter::repeat_n(true, batch.num_rows()));
                }
            }
        }
    }
    let map = UnchainedJoinHashMap::from_hashes(&hashes, valid.as_deref(), None, &segments, prefetch, reorder_ok)?;
    drop(hashes);
    reservation.shrink(transient);
    Ok(map)
}

/// Hash side: decides and, when converting, builds the contiguous index instead of the stock map. `None` means "run the
/// stock code".
#[expect(clippy::too_many_arguments)]
pub fn try_hash_csr(
    params: &HashmanParams,
    num_rows: usize,
    batches: &[RecordBatch],
    on_left: &[PhysicalExprRef],
    random_state: &RandomState,
    null_equality: NullEquality,
    reservation: &mut MemoryReservation,
    metrics: &BuildProbeJoinMetrics,
) -> Result<(Option<Box<dyn JoinHashMapType>>, Decision)> {
    if params.mode == Mode::Native {
        return Ok((None, Decision::no("native_mode")));
    }
    if num_rows > u32::MAX as usize {
        return Ok((None, Decision::no("unsupported")));
    }
    let decision = params.decide(num_rows, || sample_hashes(batches, on_left, random_state, null_equality, num_rows))?;
    if !decision.convert {
        return Ok((None, decision));
    }
    let est = HashCsr::estimate_bytes(num_rows);
    reservation.try_grow(est)?;
    metrics.build_mem_used.add(est);
    let map = build_unchained_hash(batches, on_left, random_state, null_equality, num_rows, params.prefetch, decision.reorder_ok, reservation)?;
    Ok((Some(Box::new(map)), decision))
}

/// Records the finished hash-side build (stock or converted) and hands the map back.
pub fn finish_hash_build(
    map: Box<dyn JoinHashMapType>,
    decision: &Decision,
    num_rows: usize,
    t0: Instant,
) -> Box<dyn JoinHashMapType> {
    push_record(map.hashman_kind() as usize, decision, num_rows, map.len(), map.hashman_bytes(), t0);
    map
}

/// Physical reordering of the build payload into index order (`hashman_join_reorder`): after the permutation the rows
/// of one key are consecutive, so the lookup emits index ranges and the output gather reads sequentially. Only for joins
/// that never emit unmatched build rows (their order would change); a map without a permutation hook is left alone.
pub fn maybe_reorder(
    mut map: Map,
    batch: RecordBatch,
    left_values: Vec<ArrayRef>,
    params: &HashmanParams,
    needs_final_pass: bool,
    on_left: &[PhysicalExprRef],
    reservation: &mut MemoryReservation,
) -> Result<(Map, RecordBatch, Vec<ArrayRef>)> {
    if needs_final_pass || batch.num_rows() == 0 {
        return Ok((map, batch, left_values));
    }
    let total = batch.num_rows();
    let perm = match &mut map {
        Map::HashMap(m) => m.hashman_take_permutation(total),
        Map::ArrayMap(a) => a.hashman_take_permutation(total),
    };
    let Some(perm) = perm else {
        return Ok((map, batch, left_values));
    };
    let t = Instant::now();
    let bytes = batch.get_array_memory_size();
    reservation.try_grow(bytes)?;
    let new_batch = take_record_batch(&batch, &UInt32Array::from(perm))?;
    drop(left_values);
    let new_values = evaluate_expressions_to_arrays(on_left, &new_batch)?;
    drop(batch);
    reservation.shrink(bytes);
    REORDER_NS.fetch_add(t.elapsed().as_nanos() as u64, Ordering::Relaxed);
    REORDER_ROWS.fetch_add(total as u64, Ordering::Relaxed);
    Ok((map, new_batch, new_values))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
            let mut x = self.0;
            x = (x ^ (x >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
            x = (x ^ (x >> 27)).wrapping_mul(0x94D049BB133111EB);
            x ^ (x >> 31)
        }
    }

    /// Resumable lookup with any limit must yield exactly the pairs of the unlimited lookup.
    #[test]
    fn limit_offset_protocol_matches_unlimited() {
        let mut rng = Rng(5);
        for &(n_build, space, n_probe) in &[(50usize, 7u64, 40usize), (500, 40, 300), (2000, 2000, 1000), (300, 1, 20)] {
            let build: Vec<u64> = (0..n_build).map(|_| (rng.next() % space).wrapping_mul(0x9E3779B97F4A7C15)).collect();
            let valid: Vec<bool> = (0..n_build).map(|i| i % 9 != 4).collect();
            let map = UnchainedJoinHashMap::from_hashes(&build, Some(&valid), None, &[0], n_build % 2 == 0, true).unwrap();
            let probe: Vec<u64> = (0..n_probe).map(|_| (rng.next() % (space + 3)).wrapping_mul(0x9E3779B97F4A7C15)).collect();
            let nulls = NullBuffer::from((0..n_probe).map(|i| i % 13 != 6).collect::<Vec<bool>>());
            let (mut pi, mut bi) = (vec![], vec![]);
            let end = map.get_matched_indices_with_limit_offset(&probe, Some(&nulls), usize::MAX, (0, None), &mut pi, &mut bi);
            assert_eq!(end, None);
            let mut want: Vec<(u32, u64)> = pi.iter().copied().zip(bi.iter().copied()).collect();
            want.sort();
            // naive oracle on hash equality (the exact-hash pairs are a subset of the candidates; with 64-bit
            // hashes built from a small space and the same multiplier there are no slot sharers with equal low bits)
            let mut oracle = vec![];
            for (p, &h) in probe.iter().enumerate() {
                if !nulls.is_valid(p) {
                    continue;
                }
                for (r, &bh) in build.iter().enumerate() {
                    if valid[r] && bh == h {
                        oracle.push((p as u32, r as u64));
                    }
                }
            }
            oracle.sort();
            assert_eq!(want, oracle);
            for limit in [1usize, 2, 3, 7, 64] {
                let mut got: Vec<(u32, u64)> = vec![];
                let mut off: MapOffset = (0, None);
                let mut guard = 0;
                loop {
                    let next = map.get_matched_indices_with_limit_offset(&probe, Some(&nulls), limit, off, &mut pi, &mut bi);
                    assert!(pi.len() <= limit);
                    got.extend(pi.iter().copied().zip(bi.iter().copied()));
                    match next {
                        None => break,
                        Some(o) => off = o,
                    }
                    guard += 1;
                    assert!(guard < 1_000_000, "no progress");
                }
                got.sort();
                assert_eq!(got, oracle, "limit {limit}");
            }
        }
    }
}
