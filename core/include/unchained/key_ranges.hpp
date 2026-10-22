#pragma once
#include "unchained/table.hpp"
#include <type_traits>

namespace unchained {
// Exact-key ranges inside a bucket.
//
// A bucket holds the rows of every key that hashes into it, in arbitrary order. KeyCluster sorts each bucket by the
// join key once, after the build, so that a probe can narrow a bucket to the exact run of equal keys by binary search
// (exact_key_range) instead of comparing every candidate row.
//
// Requires signed scalar keys (int32/int64) and immutable bucket bounds. `Poll` is a callable taking a bool:
// Poll(true) counts a full-key comparison, Poll(false) is only a cancellation heartbeat for long loops.
template <class Key> inline Key range_key(const Table &t, uint64_t row, uint64_t key_at) {
    Key result;
    std::memcpy(&result, t.rows + row * t.layout.stride + key_at, sizeof(result));
    return result;
}
template <class Key, class Poll> class KeyCluster {
    Table &t;
    uint64_t at;
    uint8_t *tmp;
    Poll &poll;
    bool less(Key a, Key b) {
        poll(true);
        return a < b;
    }
    bool equal(Key a, Key b) {
        poll(true);
        return a == b;
    }
    Key key(uint64_t i) const {
        return range_key<Key>(t, i, at);
    }
    void swap(uint64_t a, uint64_t b) {
        if (a == b)
            return;
        auto p = t.rows + a * t.layout.stride, q = t.rows + b * t.layout.stride;
        std::memcpy(tmp, p, t.layout.stride);
        std::memcpy(p, q, t.layout.stride);
        std::memcpy(q, tmp, t.layout.stride);
    }
    void sift(uint64_t base, uint64_t root, uint64_t count) {
        while (root < count / 2) {
            auto child = root * 2 + 1;
            if (child + 1 < count && less(key(base + child), key(base + child + 1)))
                ++child;
            if (!less(key(base + root), key(base + child)))
                return;
            swap(base + root, base + child);
            root = child;
        }
    }
    void heap(uint64_t begin, uint64_t end) {
        auto count = end - begin;
        for (auto i = count / 2; i > 0;)
            sift(begin, --i, count);
        while (count > 1) {
            swap(begin, begin + count - 1);
            sift(begin, 0, --count);
        }
    }
    void sort(uint64_t begin, uint64_t end, unsigned depth) {
        while (end - begin > 1) {
            if (!depth) {
                heap(begin, end);
                return;
            }
            --depth;
            auto a = key(begin), b = key(begin + (end - begin) / 2), c = key(end - 1);
            if (less(b, a))
                std::swap(a, b);
            if (less(c, b))
                std::swap(b, c);
            if (less(b, a))
                std::swap(a, b);
            const auto pivot = b;
            auto lower = begin, current = begin, upper = end;
            while (current < upper) {
                const auto value = key(current);
                if (less(value, pivot)) {
                    swap(current++, lower++);
                } else if (less(pivot, value))
                    swap(current, --upper);
                else
                    ++current;
            }
            // Recurse into only the smaller partition: bounded O(log n) stack.
            if (lower - begin < end - upper) {
                sort(begin, lower, depth);
                begin = upper;
            } else {
                sort(upper, end, depth);
                end = lower;
            }
        }
    }

  public:
    KeyCluster(Table &table, uint64_t offset, uint8_t *scratch, Poll &p) : t(table), at(offset), tmp(scratch), poll(p) {
        static_assert(std::is_signed<Key>::value && std::is_integral<Key>::value &&
                          (sizeof(Key) == 4 || sizeof(Key) == 8),
                      "signed SQL key required");
        if (offset > t.layout.stride || sizeof(Key) > t.layout.stride - offset || !scratch)
            throw std::invalid_argument("invalid key clustering layout/scratch");
    }
    bool run(unsigned forced_depth = std::numeric_limits<unsigned>::max()) {
        bool duplicates = false;
        for (uint64_t slot = 0; slot < t.layout.slots; ++slot) {
            poll(false);
            const auto begin = t.offset(slot), end = t.offset(slot + 1);
            if (end - begin < 2)
                continue;
            const auto first = key(begin);
            bool uniform = true;
            for (auto i = begin + 1; i < end; ++i)
                if (!equal(first, key(i))) {
                    uniform = false;
                    break;
                }
            if (uniform) {
                duplicates = true;
                continue;
            }
            unsigned depth = 0;
            for (auto n = end - begin; n > 1; n >>= 1)
                depth += 2;
            sort(begin, end, forced_depth == std::numeric_limits<unsigned>::max() ? depth : forced_depth);
            if (!duplicates)
                for (auto i = begin + 1; i < end; ++i)
                    if (equal(key(i - 1), key(i))) {
                        duplicates = true;
                        break;
                    }
        }
        return duplicates;
    }
};
#if defined(__GNUC__)
#define UNCHAINED_ALWAYS_INLINE inline __attribute__((always_inline))
#define UNCHAINED_NOINLINE __attribute__((noinline))
#else
#define UNCHAINED_ALWAYS_INLINE inline
#define UNCHAINED_NOINLINE
#endif
// Same algorithm twice: the always-inline body for a caller that wants it in its
// loop, and the out-of-line entry (v9 behaviour: a real call) that delegates to it.
template <class Key, class Poll>
UNCHAINED_ALWAYS_INLINE Table::Range exact_key_range_inline(const Table &t, Table::Range prefix, uint64_t at, Key needle,
                                                          Poll &poll) {
    if (prefix.begin == prefix.end)
        return prefix;
    const auto first = range_key<Key>(t, prefix.begin, at);
    if (prefix.end - prefix.begin == 1) {
        poll(true);
        return first == needle ? prefix : Table::Range{0, 0};
    }
    const auto last = range_key<Key>(t, prefix.end - 1, at);
    poll(true);
    if (first == last) {
        poll(true);
        return first == needle ? prefix : Table::Range{0, 0};
    }
    auto lower = prefix.begin, upper = prefix.end;
    while (lower < upper) {
        const auto middle = lower + (upper - lower) / 2;
        poll(true);
        if (range_key<Key>(t, middle, at) < needle)
            lower = middle + 1;
        else
            upper = middle;
    }
    const auto begin = lower;
    if (begin == prefix.end)
        return {0, 0};
    poll(true);
    if (range_key<Key>(t, begin, at) != needle)
        return {0, 0};
    upper = prefix.end;
    while (lower < upper) {
        const auto middle = lower + (upper - lower) / 2;
        poll(true);
        if (needle < range_key<Key>(t, middle, at))
            upper = middle;
        else
            lower = middle + 1;
    }
    return {begin, lower};
}
template <class Key, class Poll>
UNCHAINED_NOINLINE Table::Range exact_key_range(const Table &t, Table::Range prefix, uint64_t at, Key needle,
                                              Poll &poll) {
    return exact_key_range_inline<Key>(t, prefix, at, needle, poll);
}
// Exact division by a fixed stride for values known to be multiples of it:
// stride=odd*2^shift, so n*stride >> shift == n*odd and odd*inverse==1 (mod 2^64).
// Only valid for exact multiples; callers guarantee that by construction.
struct ExactDivisor {
    uint64_t shift, inverse;
    explicit ExactDivisor(uint64_t stride) : shift(0), inverse(1) {
        if (!stride)
            throw std::invalid_argument("zero stride");
        uint64_t odd = stride;
        while (!(odd & 1)) {
            odd >>= 1;
            ++shift;
        }
        for (int i = 0; i < 6; ++i)
            inverse *= 2 - odd * inverse;
        if (odd * inverse != 1)
            throw std::logic_error("stride inverse failed");
    }
    uint64_t divide(uint64_t exact_multiple) const {
        return (exact_multiple >> shift) * inverse;
    }
};
} // namespace unchained
