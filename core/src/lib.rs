//! Engine-independent index layouts for the build side of a hash join.
//!
//! Both layouts are "CSR": rows with the same bucket are stored contiguously (count -> prefix -> scatter), so
//! all candidates of a probe key are one slice instead of a pointer chain. The host owns hashing, key equality,
//! memory accounting and output; this crate only maps a hash (or an integer key index) to a slice of build rows.
//!
//! * [`HashCsr`]: directory of `slots + 1` packed words (low 48 bits: start offset; high 16 bits: a 4-of-16 tag
//!   filter) and an entry array of `hash32 << 32 | row`. The slot is taken from the top hash bits, the 32-bit
//!   filter from the bottom bits, so a run only contains rows of one slot and the filter removes most slot sharers.
//!   Candidates are a superset of the true matches; the host still verifies keys.
//! * [`ExactCsr`]: counting sort over a dense integer key range; a run contains exactly the rows of one key.

pub mod gate;

use std::fmt;

#[derive(Debug, PartialEq, Eq)]
pub enum BuildError {
    /// Row indices are stored as `u32`.
    TooManyRows(usize),
    /// A key index returned by the host is outside the declared range.
    KeyOutOfRange { row: usize, index: usize, range_len: usize },
}
impl fmt::Display for BuildError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BuildError::TooManyRows(n) => write!(f, "{n} rows exceed the u32 row index"),
            BuildError::KeyOutOfRange { row, index, range_len } => {
                write!(f, "row {row}: key index {index} outside range of {range_len}")
            }
        }
    }
}
impl std::error::Error for BuildError {}

const OFFSET_MASK: u64 = (1u64 << 48) - 1;
/// Look-ahead (in rows) of the software prefetch in the count and scatter passes.
const BUILD_PREFETCH: usize = 16;

/// Software prefetch of the cache line at `ptr` (no-op off x86_64). Never dereferences `ptr`.
#[inline(always)]
pub fn prefetch<T>(ptr: *const T) {
    #[cfg(target_arch = "x86_64")]
    // SAFETY: prefetch is a hint and cannot fault, whatever the address.
    unsafe {
        core::arch::x86_64::_mm_prefetch(ptr as *const i8, core::arch::x86_64::_MM_HINT_T0);
    }
    #[cfg(not(target_arch = "x86_64"))]
    let _ = ptr;
}

/// 4 of 16 bits chosen from hash bits 0..16 (independent of the slot bits at the top for any realistic table).
#[inline(always)]
pub fn tag(hash: u64) -> u64 {
    let a = hash & 15;
    let b = (hash >> 4) & 15;
    let c = (hash >> 8) & 15;
    let d = (hash >> 12) & 15;
    (1u64 << a) | (1u64 << b) | (1u64 << c) | (1u64 << d)
}

/// Number of directory slots for `n` entries: next power of two of n + n/8 (at least 2).
pub fn slots_for(n: usize) -> usize {
    (n + n / 8).max(2).next_power_of_two()
}

#[derive(Debug)]
pub struct HashCsr {
    shift: u32,
    dir: Vec<u64>,
    entries: Vec<u64>,
    use_tags: bool,
}

impl HashCsr {
    /// Builds the layout over `hashes`; rows for which `valid(row)` is false are not indexed.
    pub fn build<F: Fn(usize) -> bool>(hashes: &[u64], valid: F, use_tags: bool) -> Result<Self, BuildError> {
        Self::build_segmented(hashes, valid, use_tags, &[0])
    }

    /// Like [`build`](Self::build), for a row space that is a concatenation of segments (batches) whose
    /// *original* order is the reverse of the row-index order of the segments (the host indexes batches in
    /// reverse). `segments` are the ascending start offsets of the segments (first must be 0). Within one run,
    /// rows of the same hash are listed by segment descending, then row ascending: the host's original order.
    pub fn build_segmented<F: Fn(usize) -> bool>(
        hashes: &[u64],
        valid: F,
        use_tags: bool,
        segments: &[usize],
    ) -> Result<Self, BuildError> {
        let total = hashes.len();
        debug_assert!(segments.first() == Some(&0) && segments.windows(2).all(|w| w[0] <= w[1]));
        if total > u32::MAX as usize {
            return Err(BuildError::TooManyRows(total));
        }
        let mut n = 0usize;
        for row in 0..total {
            n += valid(row) as usize;
        }
        let slots = slots_for(n);
        let shift = 64 - slots.trailing_zeros();
        let mut dir = vec![0u64; slots + 1];
        for (row, &h) in hashes.iter().enumerate() {
            if let Some(&ahead) = hashes.get(row + BUILD_PREFETCH) {
                prefetch(dir.as_ptr().wrapping_add((ahead >> shift) as usize));
            }
            if valid(row) {
                let e = &mut dir[(h >> shift) as usize];
                *e += 1;
                if use_tags {
                    *e |= tag(h) << 48;
                }
            }
        }
        // inclusive prefix: low bits become the END of each slot; scatter decrements them to the START
        let mut sum = 0u64;
        for e in dir[..slots].iter_mut() {
            sum += *e & OFFSET_MASK;
            *e = (*e & !OFFSET_MASK) | sum;
        }
        dir[slots] = sum;
        let mut entries = vec![0u64; n];
        // The scatter decrements, so the first row visited ends up last in its run. Visiting segments in
        // ascending order and rows descending inside each yields: segment descending, row ascending.
        for (si, &seg_start) in segments.iter().enumerate() {
            let seg_end = segments.get(si + 1).copied().unwrap_or(total);
            for row in (seg_start..seg_end).rev() {
                if row >= seg_start + BUILD_PREFETCH {
                    prefetch(dir.as_ptr().wrapping_add((hashes[row - BUILD_PREFETCH] >> shift) as usize));
                }
                if valid(row) {
                    let h = hashes[row];
                    let e = &mut dir[(h >> shift) as usize];
                    *e -= 1;
                    entries[(*e & OFFSET_MASK) as usize] = ((h & 0xFFFF_FFFF) << 32) | row as u64;
                }
            }
        }
        Ok(HashCsr { shift, dir, entries, use_tags })
    }

    /// Number of indexed rows.
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn slots(&self) -> usize {
        self.dir.len() - 1
    }
    /// Memory held by the layout.
    pub fn bytes(&self) -> usize {
        self.dir.capacity() * 8 + self.entries.capacity() * 8
    }
    pub fn estimate_bytes(valid_rows: usize) -> usize {
        (slots_for(valid_rows) + 1) * 8 + valid_rows * 8
    }
    pub fn entries(&self) -> &[u64] {
        &self.entries
    }

    /// Entry range of the slot of `hash`; empty if the tag filter proves absence.
    #[inline(always)]
    pub fn run(&self, hash: u64) -> (usize, usize) {
        let s = (hash >> self.shift) as usize;
        // SAFETY: s < slots because shift = 64 - log2(slots); dir has slots + 1 words.
        let e = unsafe { *self.dir.get_unchecked(s) };
        if self.use_tags {
            let t = tag(hash);
            if (e >> 48) & t != t {
                return (0, 0);
            }
        }
        let end = unsafe { *self.dir.get_unchecked(s + 1) } & OFFSET_MASK;
        ((e & OFFSET_MASK) as usize, end as usize)
    }

    /// Hint the directory word of the slot of `hash` (first stage of a software-pipelined lookup).
    #[inline(always)]
    pub fn prefetch_slot(&self, hash: u64) {
        prefetch(self.dir.as_ptr().wrapping_add((hash >> self.shift) as usize));
    }

    /// Hint the first entry of the run of `hash`; call once `prefetch_slot` had time to land.
    #[inline(always)]
    pub fn prefetch_run(&self, hash: u64) {
        let s = (hash >> self.shift) as usize;
        // SAFETY: s < slots, dir has slots + 1 words.
        let start = (unsafe { *self.dir.get_unchecked(s) } & OFFSET_MASK) as usize;
        prefetch(self.entries.as_ptr().wrapping_add(start));
    }

    /// Reorders the row space into index order: afterwards the entry at position `i` refers to row `i`.
    /// Returns `perm` with `perm[new] = old` (indexed rows in entry order, then the rows that are not indexed in
    /// ascending order), so the host can permute its payload with one gather. Rows of one run stay in their
    /// original (FIFO) order.
    pub fn take_permutation(&mut self, total_rows: usize) -> Vec<u32> {
        let mut perm: Vec<u32> = Vec::with_capacity(total_rows);
        let mut indexed = vec![false; total_rows];
        for e in self.entries.iter_mut() {
            let old = *e as u32;
            indexed[old as usize] = true;
            *e = (*e & 0xFFFF_FFFF_0000_0000) | perm.len() as u64;
            perm.push(old);
        }
        for (row, &i) in indexed.iter().enumerate() {
            if !i {
                perm.push(row as u32);
            }
        }
        perm
    }

    #[inline(always)]
    pub fn entry_matches(entry: u64, hash: u64) -> bool {
        entry >> 32 == hash & 0xFFFF_FFFF
    }
    #[inline(always)]
    pub fn entry_row(entry: u64) -> u32 {
        entry as u32
    }

    /// True if some indexed row has the same (slot, low 32 bits) as `hash`.
    pub fn contains(&self, hash: u64) -> bool {
        let (a, b) = self.run(hash);
        self.entries[a..b].iter().any(|&x| Self::entry_matches(x, hash))
    }

    /// Number of distinct (slot, low 32 bits) pairs. Equals the number of distinct hashes unless two hashes share
    /// a slot and their low 32 bits (then they count once; adequate as a statistic).
    pub fn count_distinct(&self) -> usize {
        let mut distinct = 0usize;
        let mut buf: Vec<u32> = Vec::new();
        for s in 0..self.slots() {
            let a = (self.dir[s] & OFFSET_MASK) as usize;
            let b = (self.dir[s + 1] & OFFSET_MASK) as usize;
            match b - a {
                0 => {}
                1 => distinct += 1,
                _ => {
                    buf.clear();
                    buf.extend(self.entries[a..b].iter().map(|&x| (x >> 32) as u32));
                    buf.sort_unstable();
                    buf.dedup();
                    distinct += buf.len();
                }
            }
        }
        distinct
    }
}

#[derive(Debug)]
pub struct ExactCsr {
    start: Vec<u32>,
    rows: Vec<u32>,
    distinct: usize,
    identity: bool,
}

impl ExactCsr {
    /// `key_index(row)` is the bucket (key - min) of `row`, or `None` for rows that are not indexed (NULL keys).
    pub fn build<F: Fn(usize) -> Option<usize>>(rows_total: usize, range_len: usize, key_index: F) -> Result<Self, BuildError> {
        if rows_total >= u32::MAX as usize {
            return Err(BuildError::TooManyRows(rows_total));
        }
        let mut start = vec![0u32; range_len + 1];
        let mut n = 0usize;
        for row in 0..rows_total {
            if row + BUILD_PREFETCH < rows_total {
                if let Some(ahead) = key_index(row + BUILD_PREFETCH) {
                    prefetch(start.as_ptr().wrapping_add(ahead));
                }
            }
            if let Some(b) = key_index(row) {
                if b >= range_len {
                    return Err(BuildError::KeyOutOfRange { row, index: b, range_len });
                }
                start[b] += 1;
                n += 1;
            }
        }
        let mut sum = 0u32;
        let mut distinct = 0usize;
        for e in start[..range_len].iter_mut() {
            distinct += (*e != 0) as usize;
            sum += *e;
            *e = sum;
        }
        start[range_len] = sum;
        let mut rows = vec![0u32; n];
        for row in (0..rows_total).rev() {
            if row >= BUILD_PREFETCH {
                if let Some(ahead) = key_index(row - BUILD_PREFETCH) {
                    prefetch(start.as_ptr().wrapping_add(ahead));
                }
            }
            if let Some(b) = key_index(row) {
                let e = &mut start[b];
                *e -= 1;
                rows[*e as usize] = row as u32;
            }
        }
        Ok(ExactCsr { start, rows, distinct, identity: false })
    }

    pub fn len(&self) -> usize {
        if self.identity { self.start[self.start.len() - 1] as usize } else { self.rows.len() }
    }
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
    pub fn range_len(&self) -> usize {
        self.start.len() - 1
    }
    pub fn distinct(&self) -> usize {
        self.distinct
    }
    pub fn bytes(&self) -> usize {
        self.start.capacity() * 4 + self.rows.capacity() * 4
    }
    pub fn rows(&self) -> &[u32] {
        &self.rows
    }
    /// After [`take_permutation`](Self::take_permutation) the rows of bucket `b` are the positions `run(b).0..run(b).1`.
    pub fn is_identity(&self) -> bool {
        self.identity
    }

    /// Hint the start word of bucket `b` (b may be out of range; the hint never faults).
    #[inline(always)]
    pub fn prefetch_bucket(&self, b: usize) {
        prefetch(self.start.as_ptr().wrapping_add(b));
    }

    /// Hint the first row id of bucket `b`; call once `prefetch_bucket` had time to land. `b` must be `< range_len`.
    #[inline(always)]
    pub fn prefetch_rows(&self, b: usize) {
        if !self.identity {
            // SAFETY: caller guarantees b < range_len; start has range_len + 1 words.
            let a = unsafe { *self.start.get_unchecked(b) } as usize;
            prefetch(self.rows.as_ptr().wrapping_add(a));
        }
    }

    /// Makes the row space equal to index order: bucket runs become consecutive positions. Returns `perm` with
    /// `perm[new] = old` (indexed rows in run order, then the rows that are not indexed, ascending). Rows of one bucket
    /// keep their original order. Afterwards [`rows`](Self::rows) is empty and [`is_identity`](Self::is_identity) is true.
    pub fn take_permutation(&mut self, total_rows: usize) -> Vec<u32> {
        let mut perm = std::mem::take(&mut self.rows);
        let mut indexed = vec![false; total_rows];
        for &r in &perm {
            indexed[r as usize] = true;
        }
        for (row, &i) in indexed.iter().enumerate() {
            if !i {
                perm.push(row as u32);
            }
        }
        self.identity = true;
        perm
    }

    /// Row range of bucket `b` (must be `< range_len`).
    #[inline(always)]
    pub fn run(&self, b: usize) -> (usize, usize) {
        // SAFETY: caller guarantees b < range_len; start has range_len + 1 words.
        unsafe { (*self.start.get_unchecked(b) as usize, *self.start.get_unchecked(b + 1) as usize) }
    }
    pub fn contains(&self, b: usize) -> bool {
        b < self.range_len() && {
            let (a, e) = self.run(b);
            e > a
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

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

    fn check_hash_csr(hashes: &[u64], valid: &[bool], tags: bool) {
        let csr = HashCsr::build(hashes, |i| valid[i], tags).unwrap();
        assert_eq!(csr.len(), valid.iter().filter(|&&v| v).count());
        let mut by_hash: HashMap<u64, Vec<u32>> = HashMap::new();
        for (i, &h) in hashes.iter().enumerate() {
            if valid[i] {
                by_hash.entry(h).or_default().push(i as u32);
            }
        }
        // every indexed hash: all its rows are candidates (and nothing is lost by the tag filter)
        for (&h, rows) in &by_hash {
            let (a, b) = csr.run(h);
            let got: Vec<u32> = csr.entries()[a..b].iter().filter(|&&x| HashCsr::entry_matches(x, h)).map(|&x| HashCsr::entry_row(x)).collect();
            for r in rows {
                assert!(got.contains(r), "lost row {r} of hash {h:#x}");
            }
            assert!(csr.contains(h));
            // candidates are a superset only by same slot + same low 32 bits
            for &r in &got {
                let h2 = hashes[r as usize];
                assert!(valid[r as usize]);
                assert_eq!(h2 & 0xFFFF_FFFF, h & 0xFFFF_FFFF);
                assert_eq!(h2 >> csr.shift, h >> csr.shift);
            }
            // ascending row order among rows of the same hash
            let own: Vec<u32> = got.iter().copied().filter(|r| hashes[*r as usize] == h).collect();
            assert!(own.windows(2).all(|w| w[0] < w[1]));
        }
        // every valid row appears exactly once in the entry array
        let mut seen = vec![0u8; hashes.len()];
        for &x in csr.entries() {
            seen[HashCsr::entry_row(x) as usize] += 1;
        }
        for i in 0..hashes.len() {
            assert_eq!(seen[i], valid[i] as u8);
        }
        assert!(csr.count_distinct() <= by_hash.len());
        assert!(csr.count_distinct() >= by_hash.len().min(1));
    }

    #[test]
    fn hash_csr_random_differential() {
        let mut rng = Rng(1);
        for &(n, space) in &[(0usize, 1u64), (1, 1), (7, 3), (100, 5), (1000, 1000), (5000, 100_000), (20_000, u64::MAX)] {
            for tags in [false, true] {
                let hashes: Vec<u64> = (0..n)
                    .map(|_| {
                        if space == u64::MAX {
                            rng.next()
                        } else {
                            (rng.next() % space).wrapping_mul(0x9E3779B97F4A7C15)
                        }
                    })
                    .collect();
                let valid: Vec<bool> = (0..n).map(|i| i % 7 != 3).collect();
                check_hash_csr(&hashes, &valid, tags);
                let all = vec![true; n];
                check_hash_csr(&hashes, &all, tags);
            }
        }
    }

    #[test]
    fn hash_csr_segment_order_is_original_order() {
        // 3 segments of 4 rows, all rows share one hash: expected run order = segment 2 rows, segment 1, segment 0
        let hashes = vec![42u64; 12];
        let csr = HashCsr::build_segmented(&hashes, |_| true, true, &[0, 4, 8]).unwrap();
        let (a, b) = csr.run(42);
        let rows: Vec<u32> = csr.entries()[a..b].iter().map(|&x| HashCsr::entry_row(x)).collect();
        assert_eq!(rows, vec![8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3]);
    }

    #[test]
    fn hash_csr_all_equal_hash_and_collisions() {
        // every hash equal (force_hash_collisions-like): one run holds everything
        let hashes = vec![0u64; 300];
        check_hash_csr(&hashes, &vec![true; 300], true);
        // same low 32 bits, different top bits: may share a slot, must stay candidates only via the filter
        let hashes: Vec<u64> = (0..64u64).map(|i| (i << 58) | 0xDEAD_BEEF).collect();
        check_hash_csr(&hashes, &vec![true; 64], true);
    }

    #[test]
    fn hash_csr_rejects_absent_by_tag_often() {
        let mut rng = Rng(9);
        let hashes: Vec<u64> = (0..10_000).map(|_| rng.next()).collect();
        let csr = HashCsr::build(&hashes, |_| true, true).unwrap();
        let fp = (0..100_000).filter(|_| csr.contains(rng.next())).count();
        assert!(fp < 2_000, "false positives {fp}");
    }

    #[test]
    fn exact_csr_random_differential() {
        let mut rng = Rng(3);
        for &(n, range) in &[(0usize, 1usize), (1, 1), (50, 3), (1000, 10), (10_000, 100_000), (5000, 5000)] {
            let keys: Vec<Option<usize>> = (0..n).map(|i| if i % 11 == 5 { None } else { Some((rng.next() % range as u64) as usize) }).collect();
            let csr = ExactCsr::build(n, range, |i| keys[i]).unwrap();
            let mut naive: HashMap<usize, Vec<u32>> = HashMap::new();
            for (i, k) in keys.iter().enumerate() {
                if let Some(k) = k {
                    naive.entry(*k).or_default().push(i as u32);
                }
            }
            assert_eq!(csr.len(), naive.values().map(|v| v.len()).sum::<usize>());
            assert_eq!(csr.distinct(), naive.len());
            for b in 0..range {
                let (a, e) = csr.run(b);
                let got = &csr.rows()[a..e];
                match naive.get(&b) {
                    Some(rows) => assert_eq!(got, &rows[..]),
                    None => assert!(got.is_empty()),
                }
                assert_eq!(csr.contains(b), naive.contains_key(&b));
            }
            assert!(!csr.contains(range));
        }
    }

    #[test]
    fn exact_csr_take_permutation_keeps_runs_and_order() {
        let mut rng = Rng(11);
        for &(n, range) in &[(0usize, 1usize), (7, 3), (2000, 10), (5000, 3000)] {
            let keys: Vec<Option<usize>> = (0..n).map(|i| if i % 9 == 4 { None } else { Some((rng.next() % range as u64) as usize) }).collect();
            let mut csr = ExactCsr::build(n, range, |i| keys[i]).unwrap();
            let before: Vec<Vec<u32>> = (0..range).map(|b| { let (a, e) = csr.run(b); csr.rows()[a..e].to_vec() }).collect();
            let indexed = csr.len();
            let perm = csr.take_permutation(n);
            assert!(csr.is_identity());
            assert_eq!(csr.len(), indexed);
            let mut seen = perm.clone();
            seen.sort_unstable();
            assert_eq!(seen, (0..n as u32).collect::<Vec<_>>());
            for b in 0..range {
                let (a, e) = csr.run(b);
                assert_eq!(&perm[a..e], &before[b][..], "bucket {b}");
            }
            let tail = &perm[indexed..];
            assert!(tail.windows(2).all(|w| w[0] < w[1]));
            assert!(tail.iter().all(|&r| keys[r as usize].is_none()));
        }
    }

    #[test]
    fn hash_csr_take_permutation_makes_rows_positions() {
        let mut rng = Rng(13);
        let n = 3000;
        let hashes: Vec<u64> = (0..n).map(|_| (rng.next() % 200).wrapping_mul(0x9E3779B97F4A7C15)).collect();
        let valid: Vec<bool> = (0..n).map(|i| i % 8 != 3).collect();
        let mut csr = HashCsr::build_segmented(&hashes, |i| valid[i], true, &[0, 1000, 2000]).unwrap();
        let runs: Vec<(u64, Vec<u32>)> = (0..200u64)
            .map(|k| {
                let h = k.wrapping_mul(0x9E3779B97F4A7C15);
                let (a, b) = csr.run(h);
                (h, csr.entries()[a..b].iter().filter(|&&x| HashCsr::entry_matches(x, h)).map(|&x| HashCsr::entry_row(x)).collect())
            })
            .collect();
        let perm = csr.take_permutation(n);
        let mut sorted = perm.clone();
        sorted.sort_unstable();
        assert_eq!(sorted, (0..n as u32).collect::<Vec<_>>());
        for (h, old_rows) in runs {
            let (a, b) = csr.run(h);
            let new_rows: Vec<u32> = csr.entries()[a..b].iter().filter(|&&x| HashCsr::entry_matches(x, h)).map(|&x| HashCsr::entry_row(x)).collect();
            let mapped: Vec<u32> = new_rows.iter().map(|&p| perm[p as usize]).collect();
            assert_eq!(mapped, old_rows);
        }
    }

    #[test]
    fn exact_csr_rejects_out_of_range() {
        let err = ExactCsr::build(3, 2, |i| Some(i)).unwrap_err();
        assert_eq!(err, BuildError::KeyOutOfRange { row: 2, index: 2, range_len: 2 });
    }
}
