#include "unchained/key_ranges.hpp"
#include <iostream>
#include <random>
#include <vector>
struct Row {
    uint64_t hash;
    std::vector<uint8_t> bytes;
};
struct Source {
    const std::vector<Row> &rows;
    template <class F> void each(F f) const {
        for (const auto &r : rows)
            f(r.hash, r.bytes.data());
    }
};
struct Poll {
    uint64_t calls = 0;
    uint64_t stop = UINT64_MAX;
    void operator()(bool) {
        if (++calls == stop)
            throw std::runtime_error("cancelled");
    }
};
void require(bool ok) {
    if (!ok)
        throw std::runtime_error("range differential failed");
}
template <class Key> size_t test() {
    using namespace unchained;
    size_t count = 0;
    std::mt19937_64 random(20261028);
    for (size_t n : {size_t(0), size_t(1), size_t(2), size_t(7), size_t(257), size_t(2048)})
        for (size_t width : {size_t(9), size_t(13), size_t(21), size_t(33)})
            for (bool packed : {false, true})
                for (bool collision : {false, true})
                    for (bool heap : {false, true})
                        for (bool unique : {false, true}) {
                            std::vector<Row> rows;
                            std::vector<Key> keys = {std::numeric_limits<Key>::min(), std::numeric_limits<Key>::max(),
                                                     Key(-1), Key(0), Key(1)};
                            for (size_t i = 0; i < n; ++i) {
                                const Key key = unique ? Key(n - i) : (i % 3 ? keys[random() % keys.size()] : Key(i));
                                Row row;
                                row.hash = collision ? 0 : uint64_t(key) * 0x9e3779b97f4a7c15ULL;
                                row.bytes.resize(width);
                                for (auto &b : row.bytes)
                                    b = uint8_t(random());
                                std::memcpy(row.bytes.data() + 1, &key, sizeof(key));
                                rows.push_back(row);
                                keys.push_back(key);
                            }
                            const auto original = rows;
                            Layout layout(n, width, 0, packed);
                            std::vector<uint64_t> memory((layout.bytes + 7) / 8), cursors(layout.slots);
                            Table table(layout, memory.data(), true);
                            build(table, Source{rows}, cursors.data());
                            rows.clear();
                            rows.shrink_to_fit();
                            std::vector<uint8_t> scratch(width);
                            Poll poll;
                            const bool duplicates =
                                KeyCluster<Key, Poll>(table, 1, scratch.data(), poll).run(heap ? 0 : UINT32_MAX);
                            bool expected_duplicate = false;
                            for (size_t i = 0; i < original.size(); ++i)
                                for (size_t j = 0; j < i; ++j) {
                                    Key a, b;
                                    std::memcpy(&a, original[i].bytes.data() + 1, sizeof(a));
                                    std::memcpy(&b, original[j].bytes.data() + 1, sizeof(b));
                                    if (a == b) {
                                        expected_duplicate = true;
                                        break;
                                    }
                                    if (expected_duplicate)
                                        break;
                                }
                            require(duplicates == expected_duplicate);
                            keys.push_back(Key(12345));
                            for (auto key : keys) {
                                const auto hash = collision ? 0 : uint64_t(key) * 0x9e3779b97f4a7c15ULL;
                                const auto range = exact_key_range<Key>(table, table.candidates(hash), 1, key, poll);
                                { // The inline body must be the identical algorithm, comparisons included.
                                    Poll first, second;
                                    const auto a = exact_key_range<Key>(table, table.candidates(hash), 1, key, first);
                                    const auto b =
                                        exact_key_range_inline<Key>(table, table.candidates(hash), 1, key, second);
                                    require(a.begin == b.begin && a.end == b.end && first.calls == second.calls);
                                }
                                std::vector<std::vector<uint8_t>> actual, expected;
                                for (auto i = range.begin; i < range.end; ++i) {
                                    require(range_key<Key>(table, i, 1) == key);
                                    actual.emplace_back(table.rows + i * width, table.rows + (i + 1) * width);
                                }
                                for (const auto &r : original) {
                                    Key found;
                                    std::memcpy(&found, r.bytes.data() + 1, sizeof(found));
                                    if (found == key)
                                        expected.push_back(r.bytes);
                                }
                                std::sort(actual.begin(), actual.end());
                                std::sort(expected.begin(), expected.end());
                                require(actual == expected);
                            }
                            if (n > 7) {
                                Poll cancel;
                                cancel.stop = n >= 257 && collision ? 100 : 2;
                                bool thrown = false;
                                try {
                                    KeyCluster<Key, Poll>(table, 1, scratch.data(), cancel).run();
                                } catch (const std::runtime_error &) {
                                    thrown = true;
                                }
                                require(thrown);
                            }
                            ++count;
                        }
    return count;
}
size_t test_exact_divisor() {
    size_t count = 0;
    // Every stride up to 4096 (all odd parts/shifts), counts spanning the 48-bit directory limit.
    for (uint64_t stride = 1; stride <= 4096; ++stride) {
        const unchained::ExactDivisor divisor(stride);
        for (uint64_t n : {uint64_t(0), uint64_t(1), uint64_t(2), uint64_t(3), uint64_t(7), uint64_t(1000),
                           uint64_t(65535), uint64_t(1) << 20, (uint64_t(1) << 20) + 1, (uint64_t(1) << 32) - 1,
                           (uint64_t(1) << 40) + 12345, (uint64_t(1) << 48) - 1}) {
            require(divisor.divide(n * stride) == n);
            ++count;
        }
    }
    for (uint64_t stride : {uint64_t(1) << 20, (uint64_t(1) << 20) + 1, uint64_t(1000003), (uint64_t(1) << 31) - 1}) {
        const unchained::ExactDivisor divisor(stride);
        for (uint64_t n = 0; n < 5000; ++n) {
            require(divisor.divide(n * stride) == n);
            ++count;
        }
    }
    bool thrown = false;
    try {
        unchained::ExactDivisor zero(0);
    } catch (const std::invalid_argument &) {
        thrown = true;
    }
    require(thrown);
    return count;
}
int main() {
    std::cout << "PASS " << test<int32_t>() + test<int64_t>() + test_exact_divisor()
              << " exact range/sort/heap/payload/collision/lifetime/divisor cases\n";
}
