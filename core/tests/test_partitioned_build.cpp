// Differential test: the partitioned packed48 build must be byte-identical to the unpartitioned build for every
// partition count.
//   g++ -std=c++17 -O1 -g -fsanitize=address,undefined -Icore/include core/tests/test_partitioned_build.cpp -o
//   /tmp/test_partitioned_build && /tmp/test_partitioned_build
#include "unchained/partitioned_build.hpp"
#include <array>
#include <iostream>
#include <random>
#include <vector>

struct ByteRow {
    uint64_t hash;
    std::array<uint8_t, 33> bytes;
};
struct ByteSource {
    const std::vector<ByteRow> &rows;
    template <class F> void each(F f) const {
        for (const auto &row : rows)
            f(row.hash, row.bytes.data());
    }
};
struct GroupedSource {
    const unchained::PartitionedInput &g;
    size_t stride;
    uint64_t rows_in(size_t p) const {
        return g.begin[p + 1] - g.begin[p];
    }
    template <class F> void each(size_t p, F f) const {
        for (uint64_t i = g.begin[p]; i < g.begin[p + 1] && i < g.hashes.size(); ++i)
            f(g.hashes[i], g.rows.data() + i * stride);
    }
};
struct BadSource {
    const unchained::PartitionedInput &g;
    uint64_t rows_in(size_t p) const {
        return g.begin[p + 1] - g.begin[p];
    }
    template <class F> void each(size_t p, F f) const {
        for (uint64_t i = g.begin[p]; i < g.begin[p + 1] && i < g.hashes.size(); ++i)
            f(g.hashes[i], g.rows.data() + i * 33);
    }
};
static void require(bool ok, const char *what) {
    if (!ok) {
        std::cerr << "FAILED: " << what << "\n";
        std::exit(1);
    }
}

int main() {
    using namespace unchained;
    std::mt19937_64 random(20261021);
    size_t cases = 0;
    for (size_t n : {size_t(0), size_t(1), size_t(7), size_t(64), size_t(257), size_t(5000)})
        for (int mode = 0; mode < 3; ++mode) // 0: distinct-ish keys, 1: heavy duplicates, 2: all hashes equal
            for (bool bloom : {false, true})
                for (unsigned skip : {0u, 16u}) {
                    std::vector<ByteRow> rows(n);
                    for (auto &r : rows) {
                        const uint64_t key = mode == 0 ? random() : random() % 19;
                        r.hash = mode == 2 ? 0x8000000000000000ULL : key * 0x9e3779b97f4a7c15ULL;
                        for (auto &b : r.bytes)
                            b = uint8_t(random());
                    }
                    Layout layout(n, 33, 0, true, skip);
                    std::vector<uint64_t> ref_mem((layout.bytes + 7) / 8), cursors(layout.slots);
                    Table ref(layout, ref_mem.data(), bloom);
                    build(ref, ByteSource{rows}, cursors.data());
                    for (unsigned bits = 0; bits <= log2_slots(layout) && bits <= 10; ++bits) {
                        std::vector<uint64_t> mem((layout.bytes + 7) / 8);
                        Table t(layout, mem.data(), bloom);
                        PartitionedInput scratch;
                        build_partitioned(t, ByteSource{rows}, bits, scratch);
                        require(std::memcmp(ref.offsets, t.offsets, (layout.slots + 1) * 8) == 0, "directory differs");
                        require(std::memcmp(ref.rows, t.rows, n * layout.stride) == 0, "row area differs");
                        ++cases;
                        std::vector<uint64_t> mem2((layout.bytes + 7) / 8), mem3((layout.bytes + 7) / 8);
                        Table t2(layout, mem2.data(), bloom), t3(layout, mem3.data(), bloom);
                        PartitionedInput scratch2;
                        build_partitioned<33>(t2, ByteSource{rows}, bits, scratch2); // compile-time row size
                        require(std::memcmp(ref.offsets, t2.offsets, (layout.slots + 1) * 8) == 0 &&
                                    std::memcmp(ref.rows, t2.rows, n * layout.stride) == 0,
                                "fixed-size partitioned build differs");
                        ++cases;
                    }
                    for (unsigned bits = 0; bits <= log2_slots(layout) && bits <= 6;
                         ++bits) { // source already grouped by partition (host radix sink)
                        PartitionedInput grouped;
                        partition_source(layout, ByteSource{rows}, bits, grouped);
                        std::vector<uint64_t> mem5((layout.bytes + 7) / 8);
                        Table t5(layout, mem5.data(), bloom);
                        build_by_partitions(t5, GroupedSource{grouped, 33}, bits);
                        require(std::memcmp(ref.offsets, t5.offsets, (layout.slots + 1) * 8) == 0 &&
                                    std::memcmp(ref.rows, t5.rows, n * layout.stride) == 0,
                                "build_by_partitions differs");
                        std::vector<uint64_t> mem6((layout.bytes + 7) / 8);
                        Table t6(layout, mem6.data(), bloom);
                        build_by_partitions<33>(t6, GroupedSource{grouped, 33}, bits);
                        require(std::memcmp(ref.offsets, t6.offsets, (layout.slots + 1) * 8) == 0 &&
                                    std::memcmp(ref.rows, t6.rows, n * layout.stride) == 0,
                                "fixed build_by_partitions differs");
                        cases += 2;
                    }
                    {
                        std::vector<uint64_t> mem4((layout.bytes + 7) / 8);
                        Table t4(layout, mem4.data(), bloom);
                        build_packed<33>(t4, ByteSource{rows});
                        require(std::memcmp(ref.offsets, t4.offsets, (layout.slots + 1) * 8) == 0 &&
                                    std::memcmp(ref.rows, t4.rows, n * layout.stride) == 0,
                                "build_packed differs");
                        ++cases;
                    }
                }
    // wrong source cardinality must be detected, not silently accepted
    {
        std::vector<ByteRow> rows(10);
        for (auto &r : rows) {
            r.hash = random();
            r.bytes.fill(1);
        }
        Layout layout(11, 33, 0, true);
        std::vector<uint64_t> mem((layout.bytes + 7) / 8);
        Table t(layout, mem.data(), true);
        PartitionedInput scratch;
        bool threw = false;
        try {
            build_partitioned(t, ByteSource{rows}, 2, scratch);
        } catch (const std::runtime_error &) {
            threw = true;
        }
        require(threw, "cardinality mismatch not detected");
    }
    { // a source whose partition contents do not match the slot ranges must throw, not build a wrong table
        std::vector<ByteRow> rows(64);
        for (auto &r : rows) {
            r.hash = random();
            r.bytes.fill(2);
        }
        Layout layout(64, 33, 0, true, 16);
        PartitionedInput g;
        partition_source(layout, ByteSource{rows}, 2, g);
        std::swap(g.begin[1], g.begin[2]); // corrupt the boundaries
        std::vector<uint64_t> mem((layout.bytes + 7) / 8);
        Table t(layout, mem.data(), true);
        bool threw = false;
        try {
            build_by_partitions(t, BadSource{g}, 2);
        } catch (const std::exception &) {
            threw = true;
        }
        require(threw, "misgrouped partition source not detected");
    }
    std::cout << "partitioned build: " << cases << " byte-identical cases\n";
    return 0;
}
