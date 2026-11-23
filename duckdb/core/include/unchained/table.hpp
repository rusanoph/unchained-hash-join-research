#pragma once
// Unchained hash table layout (Birler, Schmidt, Fent, Neumann: "Simple, Efficient, and Robust Hash Tables for Join
// Processing", DaMoN 2024), re-implemented independently.
//
// The build side is stored as fixed-size rows laid out contiguously and grouped by directory bucket:
//
//   offsets[b] .. offsets[b+1]   the rows whose hash falls into bucket b
//   (packed layout) the upper 16 bits of offsets[b] hold a 16-bit filter ("tag") of the hashes in the bucket, so a
//   probe that cannot match is rejected without touching the rows.
//
// Construction is count -> prefix sum -> scatter (see build()). The host (a database engine) owns memory, hashing,
// SQL equality and scheduling; this header only knows about hashes and opaque rows of `stride` bytes.
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace unchained {
inline uint64_t checked_add(uint64_t a, uint64_t b) {
    if (b > std::numeric_limits<uint64_t>::max() - a)
        throw std::overflow_error("size addition overflow");
    return a + b;
}
inline uint64_t checked_mul(uint64_t a, uint64_t b) {
    if (a && b > std::numeric_limits<uint64_t>::max() / a)
        throw std::overflow_error("size multiplication overflow");
    return a * b;
}
inline uint64_t next_power_two(uint64_t n) {
    if (!n)
        return 1;
    if (n > (uint64_t(1) << 63))
        throw std::overflow_error("directory exceeds 2^63");
    --n;
    for (unsigned shift = 1; shift < 64; shift *= 2)
        n |= n >> shift;
    return n + 1;
}
inline uint64_t slots_for(uint64_t n) {
    return next_power_two(checked_add(n, checked_add(n, 7) / 8));
}
inline uint64_t align64(uint64_t n) {
    return checked_add(n, 63) & ~uint64_t(63);
}
inline uint16_t tag(uint64_t hash) {
    static const std::array<uint16_t, 2048> tags = [] {
        std::array<uint16_t, 2048> result = {};
        size_t n = 0;
        for (unsigned a = 0; a < 13; ++a)
            for (unsigned b = a + 1; b < 14; ++b)
                for (unsigned c = b + 1; c < 15; ++c)
                    for (unsigned d = c + 1; d < 16; ++d)
                        result[n++] = uint16_t((1u << a) | (1u << b) | (1u << c) | (1u << d));
        for (size_t i = n; i < result.size(); ++i)
            result[i] = result[i - n];
        return result;
    }();
    return tags[hash & 2047];
}
struct Layout {
    uint64_t count, slots, stride, tags_at, rows_at, bytes;
    unsigned shift;
    bool packed;
    // The directory slot is taken from the hash bits just below the top `skip` bits (default 0: the top bits, as in the
    // paper). DuckDB reserves the top 16 bits of its hash as a salt and radix-partitions the build side on the next
    // bits (44..47); with skip = 16 those partitions are contiguous ranges of directory slots.
    unsigned skip;
    Layout(uint64_t n, uint64_t width, uint64_t requested_slots = 0, bool compact = false, unsigned salt_skip = 0)
        : count(n), slots(requested_slots ? requested_slots : slots_for(n)), stride(width), shift(64), packed(compact),
          skip(salt_skip) {
        if (!width || !slots || (slots & (slots - 1)))
            throw std::invalid_argument("invalid row stride/directory");
        if (packed && n >= (uint64_t(1) << 48))
            throw std::overflow_error("packed directory exceeds 48-bit count");
        for (uint64_t s = slots; s > 1; s >>= 1)
            --shift;
        if (skip > 32 || (64 - shift) + skip > 64)
            throw std::invalid_argument("directory plus skipped bits exceed the hash width");
        tags_at = checked_mul(checked_add(slots, 1), 8);
        rows_at = align64(packed ? tags_at : checked_add(tags_at, checked_mul(slots, 2)));
        bytes = checked_add(rows_at, checked_mul(n, width));
        if (bytes > std::numeric_limits<size_t>::max())
            throw std::overflow_error("layout exceeds address space");
    }
    uint64_t bucket(uint64_t hash) const {
        return shift == 64 ? 0 : (hash << skip) >> shift;
    }
};
struct Table {
    Layout layout;
    uint64_t *offsets;
    uint16_t *tags;
    uint8_t *rows;
    bool bloom;
    Table(const Layout &l, void *memory, bool filter)
        : layout(l), offsets(static_cast<uint64_t *>(memory)),
          tags(l.packed ? nullptr : reinterpret_cast<uint16_t *>(static_cast<uint8_t *>(memory) + l.tags_at)),
          rows(static_cast<uint8_t *>(memory) + l.rows_at), bloom(filter) {}
    struct Range {
        uint64_t begin, end;
    };
    static constexpr uint64_t OFFSET_MASK = (uint64_t(1) << 48) - 1;
    uint64_t offset(uint64_t slot) const {
        return layout.packed ? offsets[slot] & OFFSET_MASK : offsets[slot];
    }
    uint16_t bucket_tag(uint64_t slot) const {
        return layout.packed ? uint16_t(offsets[slot] >> 48) : tags[slot];
    }
    Range candidates(uint64_t hash) const {
        const auto s = layout.bucket(hash);
        if (bloom && (bucket_tag(s) & tag(hash)) != tag(hash))
            return {0, 0};
        return {offset(s), offset(s + 1)};
    }
};
template <class Source> void build(Table &table, const Source &source, uint64_t *write_cursors) {
    std::fill(table.offsets, table.offsets + table.layout.slots + 1, uint64_t(0));
    if (table.layout.packed) {
        // Immutable source on both passes. Ends become starts by reverse scatter;
        // each bucket's 16 high tag bits survive low48 decrements unchanged.
        uint64_t seen = 0;
        source.each([&](uint64_t hash, const uint8_t *) {
            if (++seen > table.layout.count)
                throw std::runtime_error("build source cardinality changed");
            auto &entry = table.offsets[table.layout.bucket(hash)];
            ++entry;
            if (table.bloom)
                entry |= uint64_t(tag(hash)) << 48;
        });
        if (seen != table.layout.count)
            throw std::runtime_error("build source cardinality changed");
        uint64_t total = 0;
        for (uint64_t s = 0; s < table.layout.slots; ++s) {
            auto &entry = table.offsets[s];
            total = checked_add(total, entry & Table::OFFSET_MASK);
            entry = (entry & ~Table::OFFSET_MASK) | total;
        }
        table.offsets[table.layout.slots] = total;
        seen = 0;
        source.each([&](uint64_t hash, const uint8_t *row) {
            if (++seen > table.layout.count)
                throw std::runtime_error("scatter source cardinality changed");
            auto &entry = table.offsets[table.layout.bucket(hash)];
            const auto end = entry & Table::OFFSET_MASK;
            if (!end || end > table.layout.count)
                throw std::runtime_error("packed scatter out of bounds");
            --entry;
            std::memcpy(table.rows + (end - 1) * table.layout.stride, row, table.layout.stride);
        });
        if (seen != table.layout.count)
            throw std::runtime_error("scatter source cardinality changed");
        return;
    }
    std::fill(table.tags, table.tags + table.layout.slots, uint16_t(0));
    uint64_t seen = 0;
    source.each([&](uint64_t hash, const uint8_t *) {
        const auto s = table.layout.bucket(hash);
        ++table.offsets[s + 1];
        if (table.bloom)
            table.tags[s] |= tag(hash);
        ++seen;
    });
    if (seen != table.layout.count)
        throw std::runtime_error("build source cardinality changed");
    for (uint64_t s = 0; s < table.layout.slots; ++s) {
        table.offsets[s + 1] = checked_add(table.offsets[s + 1], table.offsets[s]);
        write_cursors[s] = table.offsets[s];
    }
    source.each([&](uint64_t hash, const uint8_t *row) {
        const auto s = table.layout.bucket(hash);
        const auto pos = write_cursors[s]++;
        if (pos >= table.offsets[s + 1])
            throw std::runtime_error("scatter exceeds bucket");
        std::memcpy(table.rows + pos * table.layout.stride, row, table.layout.stride);
    });
    for (uint64_t s = 0; s < table.layout.slots; ++s)
        if (write_cursors[s] != table.offsets[s + 1])
            throw std::runtime_error("incomplete scatter");
}
} // namespace unchained
