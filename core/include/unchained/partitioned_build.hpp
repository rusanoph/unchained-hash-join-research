#pragma once
// Partitioned build for the packed48 layout.
//
// Why: for a large table the scatter pass of build() writes rows to random places in memory (cache and TLB misses),
// which dominated the build cost in our measurements (6-7 ns/row when the table fits the cache, 45-50 ns/row at
// 4-8M rows). Building one partition at a time keeps those writes inside a cache-sized slice.
//
// The result is BYTE-IDENTICAL to unchained::build() on the same source:
// partitioning is by the top `bits` bits of the hash, i.e. by a contiguous range of directory buckets, it is stable
// (source order is kept inside a partition) and the in-bucket order of the reverse scatter is the same. Only the memory
// access pattern changes: the random increments of the directory and the random row writes of the scatter stay inside
// one partition (a slice of the directory and one contiguous range of the row area), which fits a cache level when
// `bits` is large enough.
#include "unchained/table.hpp"
#include <vector>

namespace unchained {
struct PartitionedInput {
    unsigned bits = 0;
    std::vector<uint64_t> begin;  // 2^bits + 1 row positions
    std::vector<uint64_t> hashes; // partition-ordered copy of the hashes
    std::vector<uint8_t> rows;    // partition-ordered copy of the rows
};
// Paper Sec. 3.4: small tuples are copied with a few fixed-size loads/stores; a runtime stride turns every row copy
// into a library call.
template <size_t FIXED> struct RowCopy {
    static void copy(uint8_t *dst, const uint8_t *src, size_t) {
        std::memcpy(dst, src, FIXED);
    }
};
template <> struct RowCopy<0> {
    static void copy(uint8_t *dst, const uint8_t *src, size_t stride) {
        std::memcpy(dst, src, stride);
    }
};
template <size_t FIXED> inline void copy_row(uint8_t *dst, const uint8_t *src, size_t stride) {
    RowCopy<FIXED>::copy(dst, src, stride);
}
inline uint64_t partition_of(uint64_t hash, unsigned bits, unsigned skip = 0) {
    return bits ? (hash << skip) >> (64 - bits) : 0;
}
inline unsigned log2_slots(const Layout &l) {
    return 64 - l.shift;
}

// Pass 1 + 2: histogram on the hash prefix, then stable scatter of (hash,row) into partition order. This is the work
// that a partitioned sink (the paper, or DuckDB's radix-partitioned sink collection) would already have done while
// collecting tuples.
template <size_t FIXED = 0, class Source>
void partition_source(const Layout &layout, const Source &source, unsigned bits, PartitionedInput &out) {
    if (bits > log2_slots(layout))
        throw std::invalid_argument("more partition bits than directory bits");
    if (bits > 20)
        throw std::invalid_argument("too many partitions");
    out.bits = bits;
    const size_t parts = size_t(1) << bits;
    out.begin.assign(parts + 1, 0);
    uint64_t seen = 0;
    source.each([&](uint64_t hash, const uint8_t *) {
        ++out.begin[partition_of(hash, bits, layout.skip) + 1];
        ++seen;
    });
    if (seen != layout.count)
        throw std::runtime_error("build source cardinality changed");
    for (size_t p = 0; p < parts; ++p)
        out.begin[p + 1] += out.begin[p];
    out.hashes.resize(layout.count);
    out.rows.resize(checked_mul(layout.count, layout.stride));
    std::vector<uint64_t> cursor(out.begin.begin(), out.begin.end() - 1);
    seen = 0;
    source.each([&](uint64_t hash, const uint8_t *row) {
        if (++seen > layout.count)
            throw std::runtime_error("partition source cardinality changed");
        const auto pos = cursor[partition_of(hash, bits, layout.skip)]++;
        out.hashes[pos] = hash;
        copy_row<FIXED>(out.rows.data() + pos * layout.stride, row, layout.stride);
    });
    if (seen != layout.count)
        throw std::runtime_error("partition source cardinality changed");
}

// Pass 3: per partition, count -> prefix -> reverse scatter, exactly the packed48 code of build() restricted to the
// buckets of the partition.
template <size_t FIXED = 0> inline void build_from_partitions(Table &table, const PartitionedInput &in) {
    const auto &l = table.layout;
    if (!l.packed)
        throw std::invalid_argument("partitioned build implements the packed48 layout only");
    const unsigned dir_bits = log2_slots(l), bits = in.bits, per_part_bits = dir_bits - bits;
    std::fill(table.offsets, table.offsets + l.slots + 1, uint64_t(0));
    const size_t parts = size_t(1) << bits;
    for (size_t p = 0; p < parts; ++p) {
        const uint64_t first = in.begin[p], last = in.begin[p + 1];
        for (uint64_t i = first; i < last; ++i) {
            const auto hash = in.hashes[i];
            auto &entry = table.offsets[l.bucket(hash)];
            ++entry;
            if (table.bloom)
                entry |= uint64_t(tag(hash)) << 48;
        }
        uint64_t total = first;
        const uint64_t b0 = uint64_t(p) << per_part_bits, b1 = uint64_t(p + 1) << per_part_bits;
        for (uint64_t s = b0; s < b1; ++s) {
            auto &entry = table.offsets[s];
            total = checked_add(total, entry & Table::OFFSET_MASK);
            entry = (entry & ~Table::OFFSET_MASK) | total;
        }
        if (total != last)
            throw std::runtime_error("partition count mismatch");
        for (uint64_t i = first; i < last; ++i) {
            auto &entry = table.offsets[l.bucket(in.hashes[i])];
            const auto end = entry & Table::OFFSET_MASK;
            if (!end || end > l.count)
                throw std::runtime_error("partitioned scatter out of bounds");
            --entry;
            copy_row<FIXED>(table.rows + (end - 1) * l.stride, in.rows.data() + i * l.stride, l.stride);
        }
    }
    table.offsets[l.slots] = l.count;
}

template <size_t FIXED = 0, class Source>
void build_partitioned(Table &table, const Source &source, unsigned bits, PartitionedInput &scratch) {
    partition_source<FIXED>(table.layout, source, bits, scratch);
    build_from_partitions<FIXED>(table, scratch);
}

// The packed48 branch of build() with an optional fixed-size row copy (identical bytes; only the copy instruction
// sequence differs). Used by the benchmark.
template <size_t FIXED = 0, class Source> void build_packed(Table &table, const Source &source) {
    const auto &l = table.layout;
    if (!l.packed)
        throw std::invalid_argument("build_packed implements the packed48 layout only");
    std::fill(table.offsets, table.offsets + l.slots + 1, uint64_t(0));
    uint64_t seen = 0;
    source.each([&](uint64_t hash, const uint8_t *) {
        if (++seen > l.count)
            throw std::runtime_error("build source cardinality changed");
        auto &entry = table.offsets[l.bucket(hash)];
        ++entry;
        if (table.bloom)
            entry |= uint64_t(tag(hash)) << 48;
    });
    if (seen != l.count)
        throw std::runtime_error("build source cardinality changed");
    uint64_t total = 0;
    for (uint64_t s = 0; s < l.slots; ++s) {
        auto &entry = table.offsets[s];
        total = checked_add(total, entry & Table::OFFSET_MASK);
        entry = (entry & ~Table::OFFSET_MASK) | total;
    }
    table.offsets[l.slots] = total;
    seen = 0;
    source.each([&](uint64_t hash, const uint8_t *row) {
        if (++seen > l.count)
            throw std::runtime_error("scatter source cardinality changed");
        auto &entry = table.offsets[l.bucket(hash)];
        const auto end = entry & Table::OFFSET_MASK;
        if (!end || end > l.count)
            throw std::runtime_error("packed scatter out of bounds");
        --entry;
        copy_row<FIXED>(table.rows + (end - 1) * l.stride, row, l.stride);
    });
}

// Build when the SOURCE ALREADY is grouped by partition (the host's radix-partitioned sink): no copy, no extra memory.
// `src` provides
//   uint64_t rows_in(size_t p) const;                         number of rows of partition p
//   template<class F> void each(size_t p, F f) const;         f(hash, row_pointer) for every row of partition p, in
//   storage order
// and partition p must be exactly the rows whose slot lies in [p << (k - bits), (p + 1) << (k - bits)) (guaranteed when
// the host partitions on the top `bits` of the slot window, i.e. layout.skip = 16 for DuckDB's radix bits 44..47). Rows
// are read twice from the (partition-local) source, as in the unpartitioned packed build.
template <size_t FIXED = 0, class PartSource>
void build_by_partitions(Table &table, const PartSource &src, unsigned bits) {
    const auto &l = table.layout;
    if (!l.packed)
        throw std::invalid_argument("partitioned build implements the packed48 layout only");
    const unsigned dir_bits = log2_slots(l);
    if (bits > dir_bits)
        throw std::invalid_argument("more partition bits than directory bits");
    const unsigned per_part_bits = dir_bits - bits;
    std::fill(table.offsets, table.offsets + l.slots + 1, uint64_t(0));
    const size_t parts = size_t(1) << bits;
    uint64_t first = 0;
    for (size_t p = 0; p < parts; ++p) {
        const uint64_t rows = src.rows_in(p), b0 = uint64_t(p) << per_part_bits, b1 = uint64_t(p + 1) << per_part_bits;
        if (rows > l.count - first)
            throw std::runtime_error("partition counts exceed the build cardinality");
        uint64_t seen = 0;
        src.each(p, [&](uint64_t hash, const uint8_t *) {
            if (++seen > rows)
                throw std::runtime_error("partition source cardinality changed");
            const auto s = l.bucket(hash);
            if (s < b0 || s >= b1)
                throw std::runtime_error("row outside its partition's slot range");
            auto &entry = table.offsets[s];
            ++entry;
            if (table.bloom)
                entry |= uint64_t(tag(hash)) << 48;
        });
        if (seen != rows)
            throw std::runtime_error("partition source cardinality changed");
        uint64_t total = first;
        for (uint64_t s = b0; s < b1; ++s) {
            auto &entry = table.offsets[s];
            total = checked_add(total, entry & Table::OFFSET_MASK);
            entry = (entry & ~Table::OFFSET_MASK) | total;
        }
        if (total != first + rows)
            throw std::runtime_error("partition count mismatch");
        seen = 0;
        src.each(p, [&](uint64_t hash, const uint8_t *row) {
            if (++seen > rows)
                throw std::runtime_error("partition scatter cardinality changed");
            auto &entry = table.offsets[l.bucket(hash)];
            const auto end = entry & Table::OFFSET_MASK;
            if (!end || end > l.count)
                throw std::runtime_error("partitioned scatter out of bounds");
            --entry;
            copy_row<FIXED>(table.rows + (end - 1) * l.stride, row, l.stride);
        });
        if (seen != rows)
            throw std::runtime_error("partition scatter cardinality changed");
        first += rows;
    }
    if (first != l.count)
        throw std::runtime_error("build source cardinality changed");
    table.offsets[l.slots] = l.count;
}
} // namespace unchained
