// Microbenchmark of the Unchained build step on synthetic rows. Output: CSV, ns per build row.
// Variants (all produce BYTE-IDENTICAL tables; every run is compared with the reference, any difference aborts):
//   plain         packed48 build, runtime row stride, source in random hash order               (the current build,
//   source order unrelated to the directory) plain_duck    same, but the source is stably grouped by DuckDB's radix
//   partition (hash bits 44..47, 16 partitions) = what our DuckDB patch sees after Unpartition() plain_fixed   plain
//   with a compile-time row size (paper Sec. 3.4: small tuples copied with a few fixed loads/stores) part_total
//   histogram on the top `bits` of the hash + stable partition copy + per-partition count/scatter (partitioning paid
//   inside the build: "retrofit") part_only     per-partition count/scatter alone on pre-partitioned input (what a
//   partitioned sink that matches the directory would hand over: "native") part_pass     the partition pass alone
//   part_only_fixed / part_total_fixed   the same with the compile-time row size
// NOT a SQL measurement: it isolates the build kernel on synthetic rows. Row layout: stride bytes, hash given
// separately.
//   g++ -std=c++17 -O2 -march=native -Icore/include core/tests/bench_build_partitioning.cpp -o /tmp/bench_build &&
//   taskset -c 2 /tmp/bench_build 7 > out.csv
#include "unchained/partitioned_build.hpp"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <vector>

struct Src {
    const uint8_t *rows;
    const uint64_t *hashes;
    size_t n, stride;
    template <class F> void each(F f) const {
        for (size_t i = 0; i < n; ++i)
            f(hashes[i], rows + i * stride);
    }
};
static uint64_t mix(uint64_t x) {
    x ^= x >> 33;
    x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33;
    x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return x;
}
static double now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static double med(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return v[v.size() / 2];
}
static void same(const unchained::Table &a, const unchained::Table &b, size_t n, size_t stride, const char *what) {
    if (std::memcmp(a.offsets, b.offsets, (a.layout.slots + 1) * 8) || std::memcmp(a.rows, b.rows, n * stride)) {
        std::fprintf(stderr, "MISMATCH %s n=%zu\n", what, n);
        std::exit(1);
    }
}
// Same directory and, per bucket, the same multiset of rows. The DuckDB-ordered source reorders rows inside a bucket
// whenever the bucket does not contain all four partition bits (directory bits < 20), so byte identity only holds for
// the other variants.
static void equivalent(const unchained::Table &a, const unchained::Table &b, size_t n, size_t stride,
                       const char *what) {
    if (std::memcmp(a.offsets, b.offsets, (a.layout.slots + 1) * 8)) {
        std::fprintf(stderr, "MISMATCH directory %s n=%zu\n", what, n);
        std::exit(1);
    }
    for (uint64_t s = 0; s < a.layout.slots; ++s) {
        uint64_t sa = 0, sb = 0;
        for (uint64_t i = a.offset(s); i < a.offset(s + 1); ++i) {
            uint64_t ha = 1469598103934665603ULL, hb = ha;
            for (size_t k = 0; k < stride; ++k) {
                ha = (ha ^ a.rows[i * stride + k]) * 1099511628211ULL;
                hb = (hb ^ b.rows[i * stride + k]) * 1099511628211ULL;
            }
            sa += ha;
            sb += hb;
        }
        if (sa != sb) {
            std::fprintf(stderr, "MISMATCH bucket contents %s n=%zu slot=%llu\n", what, n, (unsigned long long)s);
            std::exit(1);
        }
    }
}
static void out(size_t n, size_t stride, size_t mult, unsigned bits, const char *variant, double seconds) {
    std::printf("%zu,%zu,%zu,%u,%s,%.2f\n", n, stride, mult, bits, variant, seconds / n * 1e9);
}

template <size_t FIXED> static void run(size_t n, size_t stride, size_t mult, int reps) {
    using namespace unchained;
    std::mt19937_64 rng(42);
    std::vector<uint8_t> rows(n * stride);
    std::vector<uint64_t> hashes(n);
    const size_t distinct = std::max<size_t>(n / mult, 1);
    for (size_t i = 0; i < n; ++i) {
        hashes[i] = mix(rng() % distinct); // equal keys scattered in insertion order
        for (size_t b = 0; b < stride; ++b)
            rows[i * stride + b] = uint8_t(rng());
    }
    Src src{rows.data(), hashes.data(), n, stride};
    // source grouped like DuckDB's sink after Unpartition(): stable by hash bits 44..47
    std::vector<uint8_t> rows2(n * stride);
    std::vector<uint64_t> hashes2(n);
    {
        size_t count[17] = {0};
        for (size_t i = 0; i < n; ++i)
            ++count[((hashes[i] >> 44) & 15) + 1];
        for (int p = 0; p < 16; ++p)
            count[p + 1] += count[p];
        for (size_t i = 0; i < n; ++i) {
            const auto pos = count[(hashes[i] >> 44) & 15]++;
            hashes2[pos] = hashes[i];
            std::memcpy(rows2.data() + pos * stride, rows.data() + i * stride, stride);
        }
    }
    Src duck{rows2.data(), hashes2.data(), n, stride};
    Layout layout(n, stride, 0, true);
    std::vector<uint64_t> ref_mem((layout.bytes + 7) / 8), mem((layout.bytes + 7) / 8), cursors(layout.slots);
    Table ref(layout, ref_mem.data(), true);
    std::vector<double> t;
    for (int r = 0; r < reps; ++r) {
        const double a = now();
        build(ref, src, cursors.data());
        t.push_back(now() - a);
    }
    out(n, stride, mult, 0, "plain", med(t));
    {
        std::vector<double> d, f;
        for (int r = 0; r < reps; ++r) {
            Table t1(layout, mem.data(), true);
            double a = now();
            build_packed<0>(t1, duck);
            d.push_back(now() - a);
            equivalent(ref, t1, n, stride, "plain_duck");
            Table t2(layout, mem.data(), true);
            a = now();
            build_packed<FIXED>(t2, src);
            f.push_back(now() - a);
            same(ref, t2, n, stride, "plain_fixed");
        }
        out(n, stride, mult, 0, "plain_duck", med(d));
        out(n, stride, mult, 0, "plain_fixed", med(f));
    }
    for (unsigned bits : {2u, 4u, 6u, 8u, 10u, 12u}) {
        if (bits > log2_slots(layout))
            continue;
        std::vector<double> total, only, pass, total_f, only_f;
        PartitionedInput in;
        for (int r = 0; r < reps; ++r) {
            Table t1(layout, mem.data(), true);
            double a = now();
            partition_source<0>(layout, src, bits, in);
            double b = now();
            build_from_partitions<0>(t1, in);
            double c = now();
            pass.push_back(b - a);
            only.push_back(c - b);
            total.push_back(c - a);
            same(ref, t1, n, stride, "part");
            Table t2(layout, mem.data(), true);
            a = now();
            partition_source<FIXED>(layout, src, bits, in);
            b = now();
            build_from_partitions<FIXED>(t2, in);
            c = now();
            only_f.push_back(c - b);
            total_f.push_back(c - a);
            same(ref, t2, n, stride, "part_fixed");
        }
        out(n, stride, mult, bits, "part_total", med(total));
        out(n, stride, mult, bits, "part_only", med(only));
        out(n, stride, mult, bits, "part_pass", med(pass));
        out(n, stride, mult, bits, "part_total_fixed", med(total_f));
        out(n, stride, mult, bits, "part_only_fixed", med(only_f));
    }
    std::fflush(stdout);
}

int main(int argc, char **argv) {
    const int reps = argc > 1 ? std::atoi(argv[1]) : 7;
    std::printf("n,stride,multiplicity,bits,variant,ns_per_row\n");
    for (size_t mult : {size_t(1), size_t(16)})
        for (size_t n : {size_t(1) << 16, size_t(1) << 18, size_t(1) << 20, size_t(1) << 22, size_t(1) << 23}) {
            run<16>(n, 16, mult, reps);
            run<32>(n, 32, mult, reps);
        }
    return 0;
}
