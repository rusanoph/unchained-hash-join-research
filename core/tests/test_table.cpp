#include "unchained/table.hpp"
#include <iostream>
#include <random>
#include <tuple>
#include <vector>

struct Row {
    uint64_t hash, key, payload, other;
};
struct Source {
    const std::vector<Row> &rows;
    template <class F> void each(F f) const {
        for (const auto &row : rows)
            f(row.hash, reinterpret_cast<const uint8_t *>(&row));
    }
};
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
void require(bool ok) {
    if (!ok)
        throw std::runtime_error("core differential check failed");
}
int main() {
    using namespace unchained;
    size_t cases = 0;
    std::mt19937_64 random(20261019);
    for (size_t n : {size_t(0), size_t(1), size_t(7), size_t(64), size_t(257)})
        for (bool collide : {false, true})
            for (bool bloom : {false, true})
                for (bool packed : {false, true}) {
                    std::vector<Row> rows;
                    for (size_t i = 0; i < n; ++i) {
                        auto key = random() % 19;
                        rows.push_back({collide ? 0 : key * 0x9e3779b97f4a7c15ULL, key, random(), i});
                    }
                    const auto original = rows;
                    Layout layout(n, sizeof(Row), 0, packed);
                    // uint64 words ensure the required alignment of directory and tags.
                    std::vector<uint64_t> memory((layout.bytes + 7) / 8), cursors(layout.slots);
                    Table table(layout, memory.data(), bloom);
                    build(table, Source{rows}, cursors.data());
                    // Original row storage must not be needed after scatter.
                    rows.clear();
                    rows.shrink_to_fit();
                    for (uint64_t key = 0; key < 24; ++key) {
                        const auto hash = collide ? 0 : key * 0x9e3779b97f4a7c15ULL;
                        auto range = table.candidates(hash);
                        std::vector<std::pair<uint64_t, uint64_t>> actual, expected;
                        for (auto i = range.begin; i < range.end; ++i) {
                            Row row;
                            std::memcpy(&row, table.rows + i * sizeof(Row), sizeof(row));
                            if (row.key == key)
                                actual.emplace_back(row.payload, row.other);
                        }
                        for (const auto &row : original)
                            if (row.key == key)
                                expected.emplace_back(row.payload, row.other);
                        std::sort(actual.begin(), actual.end());
                        std::sort(expected.begin(), expected.end());
                        require(actual == expected);
                    }
                    require(table.offset(0) == 0 && table.offset(layout.slots) == n);
                    ++cases;
                }
    require(Layout(0, 8, 1).bucket(UINT64_MAX) == 0);
    require(slots_for(720720) == 1048576);
    bool overflow = false;
    try {
        Layout bad(UINT64_MAX, 128);
    } catch (const std::overflow_error &) {
        overflow = true;
    }
    require(overflow);
    for (size_t width : {size_t(9), size_t(13), size_t(21), size_t(33)})
        for (size_t n : {size_t(127), size_t(1031)})
            for (size_t slots : {size_t(1), size_t(8), size_t(1024), size_t(4096)})
                for (bool bloom : {false, true}) {
                    std::vector<ByteRow> rows;
                    for (size_t i = 0; i < n; ++i) {
                        ByteRow row;
                        const uint64_t key = random() % 31;
                        row.hash = key * 0x9e3779b97f4a7c15ULL;
                        for (auto &byte : row.bytes)
                            byte = uint8_t(random());
                        std::memcpy(row.bytes.data(), &key, 8);
                        rows.push_back(row);
                    }
                    const auto original = rows;
                    Layout layout(n, width, slots, true);
                    std::vector<uint64_t> memory((layout.bytes + 7) / 8);
                    Table table(layout, memory.data(), bloom);
                    build(table, ByteSource{rows}, nullptr);
                    rows.clear();
                    rows.shrink_to_fit();
                    for (uint64_t key = 0; key < 35; ++key) {
                        std::vector<std::vector<uint8_t>> actual, expected;
                        const auto range = table.candidates(key * 0x9e3779b97f4a7c15ULL);
                        for (auto i = range.begin; i < range.end; ++i) {
                            const auto bytes = table.rows + i * width;
                            uint64_t found;
                            std::memcpy(&found, bytes, 8);
                            if (found == key)
                                actual.emplace_back(bytes, bytes + width);
                        }
                        for (const auto &row : original) {
                            uint64_t found;
                            std::memcpy(&found, row.bytes.data(), 8);
                            if (found == key)
                                expected.emplace_back(row.bytes.data(), row.bytes.data() + width);
                        }
                        std::sort(actual.begin(), actual.end());
                        std::sort(expected.begin(), expected.end());
                        require(actual == expected);
                    }
                    require(table.offset(0) == 0 && table.offset(slots) == n);
                    ++cases;
                }
    overflow = false;
    try {
        Layout bad(uint64_t(1) << 48, 1, 1, true);
    } catch (const std::overflow_error &) {
        overflow = true;
    }
    require(overflow);
    std::cout << "PASS " << cases << " fixed-row/Bloom/collision/lifetime cases\n";
}
