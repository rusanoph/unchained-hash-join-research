//! Cheap build-side statistics for the "convert or keep the stock index" decision, taken from a small sample before
//! anything is built.
//!
//! * `multiplicity`: effective rows per key (size-biased: sum m_k^2 / n), from the number of equal pairs among a random
//!   sample of rows: for uniform multiplicity m the expected pair fraction is (m - 1) / (n - 1).
//! * `locality`: in windows of consecutive rows, the fraction of rows whose key also occurs elsewhere in the same window.
//!   High for key-clustered (sorted) builds, where the stock chain already walks neighbouring rows; about 0 for builds
//!   whose rows of one key are scattered. This is the property under which a contiguous layout pays.

pub const RANDOM_SAMPLE: usize = 16_384;
pub const WINDOWS: usize = 32;
pub const WINDOW_LEN: usize = 256;

#[derive(Debug, Clone)]
pub struct SamplePlan {
    /// sorted, unique row positions
    pub random: Vec<usize>,
    /// (start, len), each inside `0..n`
    pub windows: Vec<(usize, usize)>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Estimate {
    pub multiplicity: f64,
    pub locality: f64,
    pub random_sample: usize,
}

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut x = self.0;
        x = (x ^ (x >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        x = (x ^ (x >> 27)).wrapping_mul(0x94D049BB133111EB);
        x ^ (x >> 31)
    }
    fn below(&mut self, n: usize) -> usize {
        ((self.next() as u128 * n as u128) >> 64) as usize
    }
}

/// Deterministic for a given `n`.
pub fn plan_sample(n: usize) -> SamplePlan {
    let mut rng = Rng(0xC0FFEE ^ n as u64);
    let mut random: Vec<usize> = if n <= RANDOM_SAMPLE {
        (0..n).collect()
    } else {
        (0..RANDOM_SAMPLE).map(|_| rng.below(n)).collect()
    };
    random.sort_unstable();
    random.dedup();
    let mut windows = Vec::new();
    if n > 0 {
        let len = WINDOW_LEN.min(n);
        for _ in 0..WINDOWS {
            windows.push((rng.below(n - len + 1), len));
        }
    }
    SamplePlan { random, windows }
}

/// `random_values`: keys (or hashes) of the sampled rows, rows whose key can never match omitted.
/// `window_values`: the same for each window, in row order. `n`: number of rows of the build side.
pub fn estimate(n: usize, random_values: &[u64], window_values: &[Vec<u64>]) -> Estimate {
    let mut v: Vec<u64> = random_values.to_vec();
    v.sort_unstable();
    let s = v.len();
    let mut pairs = 0u64;
    let mut i = 0;
    while i < s {
        let mut j = i + 1;
        while j < s && v[j] == v[i] {
            j += 1;
        }
        let c = (j - i) as u64;
        pairs += c * (c - 1) / 2;
        i = j;
    }
    let multiplicity = if s < 2 || n < 2 {
        1.0
    } else {
        let all_pairs = s as f64 * (s as f64 - 1.0) / 2.0;
        // the sample is without replacement from n rows: scale pair frequency back to the whole table
        1.0 + pairs as f64 / all_pairs * (n as f64 - 1.0)
    };
    let (mut dup_rows, mut rows) = (0usize, 0usize);
    let mut w: Vec<u64> = Vec::new();
    for win in window_values {
        w.clear();
        w.extend_from_slice(win);
        w.sort_unstable();
        w.dedup();
        dup_rows += win.len() - w.len();
        rows += win.len();
    }
    let locality = if rows == 0 { 0.0 } else { dup_rows as f64 / rows as f64 };
    Estimate { multiplicity, locality, random_sample: s }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn estimate_for(keys: &[u64]) -> Estimate {
        let plan = plan_sample(keys.len());
        let random: Vec<u64> = plan.random.iter().map(|&p| keys[p]).collect();
        let windows: Vec<Vec<u64>> = plan.windows.iter().map(|&(s, l)| keys[s..s + l].to_vec()).collect();
        estimate(keys.len(), &random, &windows)
    }

    fn grouped(n: usize, m: usize) -> Vec<u64> {
        (0..n).map(|i| (i / m) as u64).collect()
    }

    fn shuffled(mut v: Vec<u64>) -> Vec<u64> {
        let mut rng = Rng(7);
        for i in (1..v.len()).rev() {
            let j = rng.below(i + 1);
            v.swap(i, j);
        }
        v
    }

    #[test]
    fn plan_is_deterministic_and_in_range() {
        let a = plan_sample(1_000_000);
        let b = plan_sample(1_000_000);
        assert_eq!(a.random, b.random);
        assert!(a.random.windows(2).all(|w| w[0] < w[1]));
        assert!(a.random.iter().all(|&p| p < 1_000_000));
        assert!(a.windows.iter().all(|&(s, l)| s + l <= 1_000_000));
        assert_eq!(plan_sample(10).random.len(), 10);
        assert!(plan_sample(0).windows.is_empty());
    }

    #[test]
    fn unique_keys_have_multiplicity_one_and_no_locality() {
        let e = estimate_for(&shuffled((0..2_000_000u64).collect()));
        assert!((e.multiplicity - 1.0).abs() < 0.3, "{e:?}");
        assert!(e.locality < 0.01, "{e:?}");
    }

    #[test]
    fn shuffled_multiplicity_is_estimated() {
        for &m in &[4usize, 16, 64] {
            let e = estimate_for(&shuffled(grouped(2_000_000, m)));
            assert!(e.multiplicity > 0.6 * m as f64 && e.multiplicity < 1.6 * m as f64, "m={m} {e:?}");
            assert!(e.locality < 0.05, "m={m} {e:?}");
        }
    }

    #[test]
    fn grouped_build_has_high_locality() {
        let e = estimate_for(&grouped(2_000_000, 16));
        assert!(e.locality > 0.85, "{e:?}");
        // clustered but not sorted: groups of 16 equal keys placed at random positions are still local
        let mut keys = grouped(2_000_000, 16);
        let blocks: Vec<Vec<u64>> = keys.chunks(16).map(|c| c.to_vec()).collect();
        let order = shuffled((0..blocks.len() as u64).collect());
        keys = order.iter().flat_map(|&b| blocks[b as usize].clone()).collect();
        let e = estimate_for(&keys);
        assert!(e.locality > 0.85, "{e:?}");
    }
}
