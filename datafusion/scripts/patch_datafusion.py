#!/usr/bin/env python3
"""Generate the patched DataFusion tree from a checkout of the pinned revision.

    python3 scripts/patch_datafusion.py --pristine path/to/datafusion \
        --output build/datafusion [--default-mode native|unchained|auto] [--default-min-rows N]

Verifies the SHA256 of every upstream file it rewrites (deps/datafusion.json), refuses to reuse an output directory,
replaces each anchor exactly once, adds integrations/datafusion/hashman.rs and writes patch-manifest.json.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
PIN = json.loads((LAB / 'deps/datafusion.json').read_text())
JOINS = 'datafusion/physical-plan/src/joins'

ARRAY_METHODS = '''    /// hashman: telemetry kind (1 stock ArrayMap, 3 exact CSR).
    pub fn hashman_kind(&self) -> usize {
        if self.csr.is_some() { 3 } else { 1 }
    }

    /// hashman: build the exact CSR directly (no stock chain is built).
    pub(crate) fn hashman_try_new_csr(array: &ArrayRef, min_val: u64, max_val: u64) -> Result<Self> {
        let range = Self::calculate_range(min_val, max_val);
        if range >= usize::MAX as u64 {
            return internal_err!("ArrayMap key range is too large to be allocated.");
        }
        let size = (range + 1) as usize;
        let csr = downcast_supported_integer!(array.data_type() => (hashman_build_csr, array, min_val, size))?;
        Ok(Self { data: Vec::new(), offset: min_val, next: Vec::new(), num_of_distinct_key: csr.distinct(), csr: Some(csr), hashman_prefetch: false, hashman_reorder_ok: false })
    }

    fn hashman_build_csr<T: ArrowNumericType>(array: &ArrayRef, offset: u64, range_len: usize) -> Result<ExactCsr>
    where
        T::Native: AsPrimitive<u64>,
    {
        let arr = array.as_primitive::<T>();
        let have_null = arr.null_count() > 0;
        ExactCsr::build(arr.len(), range_len, |i| {
            if have_null && arr.is_null(i) {
                return None;
            }
            // SAFETY: i < arr.len()
            let key: u64 = unsafe { arr.value_unchecked(i) }.as_();
            Some(key.wrapping_sub(offset) as usize)
        })
        .map_err(|e| internal_datafusion_err!("hashman exact CSR build failed: {e}"))
    }

    /// hashman: keys (as u64, `None` for NULL) at the given positions, for the build-side decision sample.
    pub(crate) fn hashman_keys_at(array: &ArrayRef, positions: &[usize]) -> Result<Vec<Option<u64>>> {
        downcast_supported_integer!(array.data_type() => (hashman_keys_at_typed, array, positions))
    }

    fn hashman_keys_at_typed<T: ArrowNumericType>(array: &ArrayRef, positions: &[usize]) -> Result<Vec<Option<u64>>>
    where
        T::Native: AsPrimitive<u64>,
    {
        let arr = array.as_primitive::<T>();
        Ok(positions
            .iter()
            .map(|&p| {
                if arr.is_null(p) {
                    None
                } else {
                    // SAFETY: positions come from a sample plan over 0..arr.len()
                    Some(unsafe { arr.value_unchecked(p) }.as_())
                }
            })
            .collect())
    }

    /// hashman: options of the converted index (software prefetch in the lookup, payload permutation allowed).
    pub(crate) fn hashman_set_options(&mut self, prefetch: bool, reorder_ok: bool) {
        self.hashman_prefetch = prefetch;
        self.hashman_reorder_ok = reorder_ok;
    }

    /// hashman: payload permutation (see hashman::maybe_reorder); `None` for the stock chain.
    pub(crate) fn hashman_take_permutation(&mut self, total_rows: usize) -> Option<Vec<u32>> {
        if !self.hashman_reorder_ok {
            return None;
        }
        self.csr.as_mut().map(|c| c.take_permutation(total_rows))
    }

    fn hashman_lookup_csr<T: ArrowNumericType, const PF: bool>(
        &self,
        csr: &ExactCsr,
        array: &ArrayRef,
        limit: usize,
        current_offset: MapOffset,
        probe_indices: &mut Vec<u32>,
        build_indices: &mut Vec<u64>,
    ) -> Result<Option<MapOffset>>
    where
        T::Native: Copy + AsPrimitive<u64>,
    {
        let arr = array.as_primitive::<T>();
        let have_null = arr.null_count() > 0;
        let n = arr.len();
        let rows = csr.rows();
        let identity = csr.is_identity();
        let range_len = csr.range_len();
        let mut remaining = limit;
        // (idx, None) start at idx; (idx, Some(0)) idx finished; (idx, Some(p + 1)) continue idx at row position p
        let (mut idx, mut pending) = match current_offset {
            (i, None) => (i, None),
            (i, Some(0)) => (i + 1, None),
            (i, Some(p1)) => (i, Some(p1 as usize - 1)),
        };
        while idx < n {
            if PF {
                if idx + 16 < n {
                    // SAFETY: idx + 16 < n
                    let k: u64 = unsafe { arr.value_unchecked(idx + 16) }.as_();
                    if let Some(b) = Self::key_to_index(k, self.offset, range_len) {
                        csr.prefetch_bucket(b);
                    }
                }
                if idx + 8 < n {
                    // SAFETY: idx + 8 < n
                    let k: u64 = unsafe { arr.value_unchecked(idx + 8) }.as_();
                    if let Some(b) = Self::key_to_index(k, self.offset, range_len) {
                        csr.prefetch_rows(b);
                    }
                }
            }
            if !(have_null && arr.is_null(idx)) {
                // SAFETY: idx < n
                let key: u64 = unsafe { arr.value_unchecked(idx) }.as_();
                if let Some(b) = Self::key_to_index(key, self.offset, range_len) {
                    let (a, e) = csr.run(b);
                    let mut pos = pending.take().unwrap_or(a);
                    while pos < e {
                        let chunk = (e - pos).min(remaining);
                        probe_indices.extend(std::iter::repeat_n(idx as u32, chunk));
                        if identity {
                            build_indices.extend((pos as u64)..((pos + chunk) as u64));
                        } else {
                            build_indices.extend(rows[pos..pos + chunk].iter().map(|&r| r as u64));
                        }
                        pos += chunk;
                        remaining -= chunk;
                        if remaining == 0 {
                            return Ok(if pos >= e {
                                if idx == n - 1 { None } else { Some((idx, Some(0))) }
                            } else {
                                Some((idx, Some(pos as u64 + 1)))
                            });
                        }
                    }
                }
            }
            idx += 1;
        }
        Ok(None)
    }

'''

CONFIG_FIELDS = '''        pub perfect_hash_join_min_key_density: f64, default = 0.15

        /// hashman-datafusion-lab: build-side index of `HashJoinExec`. `native` is the stock engine,
        /// `unchained` gives every supported build side a contiguous (CSR) index, `auto` decides per join
        /// from a sample taken before building (`hashman_join_min_build_rows`,
        /// `hashman_join_min_multiplicity`, `hashman_join_max_locality`).
        pub hashman_join: String, default = "@MODE@".to_string()

        /// hashman-datafusion-lab: minimum number of build rows for the `auto` decision.
        pub hashman_join_min_build_rows: usize, default = @MINROWS@

        /// hashman-datafusion-lab: minimum effective key multiplicity (rows per key) for the `auto` decision.
        pub hashman_join_min_multiplicity: f64, default = 3.0

        /// hashman-datafusion-lab: build sides whose consecutive-row windows repeat keys more than this fraction
        /// are treated as key-clustered and keep the stock index in `auto` mode.
        pub hashman_join_max_locality: f64, default = 1.01

        /// hashman-datafusion-lab: physically reorder the build payload into index order after a conversion (joins that emit no unmatched build rows only).
        pub hashman_join_reorder: bool, default = @REORDER@

        /// hashman-datafusion-lab: software prefetch of directory slots and runs in the converted lookups.
        pub hashman_join_prefetch: bool, default = @PREFETCH@

        /// hashman-datafusion-lab: in `auto` mode keep the stock index when the estimated probe rows are below this multiple of the build rows (0 disables).
        pub hashman_join_min_probe_ratio: f64, default = @PROBERATIO@

        /// hashman-datafusion-lab: record per-lookup timings in the hashman telemetry.
        pub hashman_join_profile: bool, default = false
'''

# (file, old, new); every `old` must occur exactly once in the pristine file
EDITS = [
    ('datafusion/common/src/config.rs',
     '        pub perfect_hash_join_min_key_density: f64, default = 0.15\n', CONFIG_FIELDS),
    ('datafusion/physical-plan/Cargo.toml',
     'hashbrown = { workspace = true }\n',
     'hashbrown = { workspace = true }\nhashman-join-core = { path = "@LAB_CORE@" }\n'),
    (f'{JOINS}/mod.rs', 'pub mod join_hash_map;\n', 'pub mod join_hash_map;\npub mod hashman;\n'),
    (f'{JOINS}/join_hash_map.rs',
     '    /// Returns the number of entries in the join hash map.\n    fn len(&self) -> usize;\n}\n',
     '''    /// Returns the number of entries in the join hash map.
    fn len(&self) -> usize;

    /// hashman: telemetry kind, 0 stock chain, 2 hash CSR.
    fn hashman_kind(&self) -> u8 {
        0
    }

    /// hashman: bytes held by the index when known (0 for the stock maps).
    fn hashman_bytes(&self) -> usize {
        0
    }

    /// hashman: reorder the build row space into index order and return the permutation (`perm[new] = old`).
    fn hashman_take_permutation(&mut self, _total_rows: usize) -> Option<Vec<u32>> {
        None
    }
}
'''),
    (f'{JOINS}/array_map.rs',
     'use datafusion_common::{Result, ScalarValue, internal_err};\n',
     'use datafusion_common::{Result, ScalarValue, internal_err};\nuse datafusion_common::internal_datafusion_err;\nuse hashman_join_core::ExactCsr;\n'),
    (f'{JOINS}/array_map.rs',
     '    next: Vec<u32>,\n    num_of_distinct_key: usize,\n}',
     '    next: Vec<u32>,\n    num_of_distinct_key: usize,\n    // hashman: exact CSR replacing data/next when set\n    csr: Option<ExactCsr>,\n    hashman_prefetch: bool,\n    hashman_reorder_ok: bool,\n}'),
    (f'{JOINS}/array_map.rs',
     '        Ok(Self {\n            data,\n            offset: min_val,\n            next,\n            num_of_distinct_key,\n        })',
     '        Ok(Self {\n            data,\n            offset: min_val,\n            next,\n            num_of_distinct_key,\n            csr: None,\n            hashman_prefetch: false,\n            hashman_reorder_ok: false,\n        })'),
    (f'{JOINS}/array_map.rs',
     '    pub fn size(&self) -> usize {\n        self.data.capacity() * size_of::<u32>() + self.next.capacity() * size_of::<u32>()\n    }',
     '    pub fn size(&self) -> usize {\n        if let Some(csr) = &self.csr {\n            return csr.bytes();\n        }\n        self.data.capacity() * size_of::<u32>() + self.next.capacity() * size_of::<u32>()\n    }'),
    (f'{JOINS}/array_map.rs',
     '    fn get_value(&self, key: u64) -> Option<u32> {\n        let idx = Self::key_to_index(key, self.offset, self.data.len())?;\n',
     '    fn get_value(&self, key: u64) -> Option<u32> {\n        if let Some(csr) = &self.csr {\n            let idx = Self::key_to_index(key, self.offset, csr.range_len())?;\n            return csr.contains(idx).then_some(1);\n        }\n        let idx = Self::key_to_index(key, self.offset, self.data.len())?;\n'),
    (f'{JOINS}/array_map.rs',
     '        let have_null = arr.null_count() > 0;\n\n        if self.next.is_empty() {',
     '        let have_null = arr.null_count() > 0;\n\n        if let Some(csr) = &self.csr {\n            return if self.hashman_prefetch {\n                self.hashman_lookup_csr::<T, true>(csr, array, limit, current_offset, probe_indices, build_indices)\n            } else {\n                self.hashman_lookup_csr::<T, false>(csr, array, limit, current_offset, probe_indices, build_indices)\n            };\n        }\n\n        if self.next.is_empty() {'),
    (f'{JOINS}/array_map.rs',
     '    pub fn contain_keys(&self, probe_side_keys: &[ArrayRef]) -> Result<BooleanArray> {',
     ARRAY_METHODS + '    pub fn contain_keys(&self, probe_side_keys: &[ArrayRef]) -> Result<BooleanArray> {'),
    (f'{JOINS}/hash_join/exec.rs',
     'use crate::joins::array_map::ArrayMap;\n',
     'use crate::joins::array_map::ArrayMap;\nuse crate::joins::hashman;\n'),
    (f'{JOINS}/hash_join/exec.rs',
     '    null_equality: NullEquality,\n) -> Result<Option<(ArrayMap, RecordBatch, Vec<ArrayRef>)>> {',
     '    null_equality: NullEquality,\n    hashman_params: &hashman::HashmanParams,\n    hashman_t0: std::time::Instant,\n) -> Result<Option<(ArrayMap, RecordBatch, Vec<ArrayRef>)>> {'),
    (f'{JOINS}/hash_join/exec.rs',
     '    let array_map = ArrayMap::try_new(&left_values[0], min_val, max_val)?;\n\n    Ok(Some((array_map, batch, left_values)))',
     '    let decision = hashman::decide_array(hashman_params, &left_values[0], num_row)?;\n    let mut array_map = if decision.convert {\n        ArrayMap::hashman_try_new_csr(&left_values[0], min_val, max_val)?\n    } else {\n        ArrayMap::try_new(&left_values[0], min_val, max_val)?\n    };\n    array_map.hashman_set_options(hashman_params.prefetch, decision.reorder_ok);\n    hashman::record_array_build(&array_map, &decision, num_row, hashman_t0);\n\n    Ok(Some((array_map, batch, left_values)))'),
    (f'{JOINS}/hash_join/exec.rs',
     '    let (join_hash_map, batch, left_values) =\n        if let Some((array_map, batch, left_value)) = try_create_array_map(',
     '    let mut hashman_params = hashman::HashmanParams::from_config(&config)?;\n    hashman_params.probe_rows = hashman_probe_rows;\n    let hashman_t0 = std::time::Instant::now();\n    let (join_hash_map, batch, left_values) =\n        if let Some((array_map, batch, left_value)) = try_create_array_map('),
    (f'{JOINS}/hash_join/exec.rs',
     '            config.execution.perfect_hash_join_min_key_density,\n            null_equality,\n        )? {',
     '            config.execution.perfect_hash_join_min_key_density,\n            null_equality,\n            &hashman_params,\n            hashman_t0,\n        )? {'),
    (f'{JOINS}/hash_join/exec.rs',
     '            let mut hashmap: Box<dyn JoinHashMapType> = if num_rows > u32::MAX as usize {',
     '            let (hashman_csr, hashman_decision) = hashman::try_hash_csr(\n                &hashman_params,\n                num_rows,\n                &batches,\n                &on_left,\n                &random_state,\n                null_equality,\n                &mut reservation,\n                &metrics,\n            )?;\n            let hashman_is_forced = hashman_csr.is_some();\n            let mut hashmap: Box<dyn JoinHashMapType> = if let Some(csr_map) = hashman_csr {\n                csr_map\n            } else if num_rows > u32::MAX as usize {'),
    (f'{JOINS}/hash_join/exec.rs',
     '            for batch in batches_iter.clone() {\n                hashes_buffer.clear();',
     '            for batch in batches_iter.clone().filter(|_| !hashman_is_forced) {\n                hashes_buffer.clear();'),
    (f'{JOINS}/hash_join/exec.rs',
     '            let left_values = evaluate_expressions_to_arrays(&on_left, &batch)?;\n\n            (Map::HashMap(hashmap), batch, left_values)',
     '            let left_values = evaluate_expressions_to_arrays(&on_left, &batch)?;\n\n            let hashmap = hashman::finish_hash_build(hashmap, &hashman_decision, num_rows, hashman_t0);\n\n            (Map::HashMap(hashmap), batch, left_values)'),
    (f'{JOINS}/hash_join/exec.rs',
     '    // Reserve additional memory for visited indices bitmap and create shared builder\n',
     '    let (join_hash_map, batch, left_values) = hashman::maybe_reorder(\n        join_hash_map,\n        batch,\n        left_values,\n        &hashman_params,\n        with_visited_indices_bitmap,\n        &on_left,\n        &mut reservation,\n    )?;\n\n    // Reserve additional memory for visited indices bitmap and create shared builder\n'),
    (f'{JOINS}/hash_join/exec.rs',
     '    null_equality: NullEquality,\n    array_map_created_count: Count,\n) -> Result<JoinLeftData> {',
     '    null_equality: NullEquality,\n    array_map_created_count: Count,\n    hashman_probe_rows: Option<usize>,\n) -> Result<JoinLeftData> {'),
    (f'{JOINS}/hash_join/exec.rs',
     '                    array_map_created_count,\n                ))\n            })?,',
     '                    array_map_created_count,\n                    hashman::probe_rows_estimate(&self.right, None),\n                ))\n            })?,'),
    (f'{JOINS}/hash_join/exec.rs',
     '                    array_map_created_count,\n                ))\n            }\n            PartitionMode::Auto => {',
     '                    array_map_created_count,\n                    hashman::probe_rows_estimate(&self.right, Some(partition)),\n                ))\n            }\n            PartitionMode::Auto => {'),
    (f'{JOINS}/hash_join/stream.rs',
     'use datafusion_common::hash_utils::RandomState;\n',
     'use crate::joins::hashman;\nuse datafusion_common::hash_utils::RandomState;\n'),
    (f'{JOINS}/hash_join/stream.rs',
     '        // get the matched by join keys indices\n        let (left_indices, right_indices, next_offset) = match build_side.left_data.map()',
     '        // get the matched by join keys indices\n        let hashman_t = hashman::probe_start();\n        let (left_indices, right_indices, next_offset) = match build_side.left_data.map()'),
    (f'{JOINS}/hash_join/stream.rs',
     '        };\n\n        let distinct_right_indices_count = count_distinct_sorted_indices(&right_indices);',
     '        };\n        hashman::probe_end(hashman_t, build_side.left_data.map(), left_indices.len());\n\n        let distinct_right_indices_count = count_distinct_sorted_indices(&right_indices);'),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pristine', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--default-mode', default='native', choices=['native', 'unchained', 'auto'])
    ap.add_argument('--default-min-rows', type=int, default=200000)
    ap.add_argument('--default-probe-ratio', default='0.05')
    ap.add_argument('--default-reorder', default='true', choices=['true', 'false'])
    ap.add_argument('--default-prefetch', default='true', choices=['true', 'false'])
    a = ap.parse_args()
    pristine, out = Path(a.pristine), Path(a.output)
    if out.exists():
        sys.exit(f'refusing to reuse {out}')
    commit = subprocess.check_output(['git', '-C', str(pristine), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != PIN['commit']:
        sys.exit(f'pristine tree is at {commit}, pinned {PIN["commit"]}')
    touched = sorted({f for f, _, _ in EDITS})
    for f in touched:
        if sha(pristine / f) != PIN['files'][f]:
            sys.exit(f'{f}: pristine SHA256 differs from deps/datafusion.json')
    shutil.copytree(pristine, out, ignore=shutil.ignore_patterns('.git', 'target'), symlinks=True)
    for f in touched:
        text = (out / f).read_text()
        for ef, old, new in EDITS:
            if ef != f:
                continue
            if text.count(old) != 1:
                sys.exit(f'{f}: anchor occurs {text.count(old)} times: {old[:70]!r}')
            new = (new.replace('@MODE@', a.default_mode).replace('@MINROWS@', str(a.default_min_rows)).replace('@PROBERATIO@', a.default_probe_ratio).replace('@REORDER@', a.default_reorder).replace('@PREFETCH@', a.default_prefetch)
                   .replace('@LAB_CORE@', '../../hashman-join-core'))
            text = text.replace(old, new)
        (out / f).write_text(text)
    shutil.copy(LAB / 'integrations/datafusion/hashman.rs', out / JOINS / 'hashman.rs')
    shutil.copytree(LAB / 'core', out / 'hashman-join-core', ignore=shutil.ignore_patterns('target', 'Cargo.lock'))
    manifest = {
        'schema_version': 1, 'pristine_commit': commit, 'default_mode': a.default_mode,
        'default_min_rows': a.default_min_rows, 'default_probe_ratio': a.default_probe_ratio, 'default_reorder': a.default_reorder, 'default_prefetch': a.default_prefetch, 'edits': len(EDITS),
        'lab_core_sha256': hashlib.sha256(b''.join(p.read_bytes() for p in sorted((LAB / 'core').rglob('*.rs')))).hexdigest(),
        'patched_files': {f: sha(out / f) for f in touched + [f'{JOINS}/hashman.rs']},
    }
    (out / 'patch-manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    print(json.dumps(manifest, indent=1))


if __name__ == '__main__':
    main()
