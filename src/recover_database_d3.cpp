#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

static constexpr uint32_t kSeedXor = 0x2378BF41u;
static constexpr size_t kRawPrefix = 0x100;
static constexpr uint32_t kLoadBase = 0x1000;

struct Database {
    std::vector<uint8_t> bytes;
    uint32_t stored_checksum;
    uint32_t end_address;
};

static uint32_t be32(const uint8_t* p) {
    return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) |
           (uint32_t(p[2]) << 8) | uint32_t(p[3]);
}

static bool load_database(const char* path, Database* db) {
    FILE* file = nullptr;
    if (fopen_s(&file, path, "rb") != 0 || !file) return false;
    _fseeki64(file, 0, SEEK_END);
    const auto size = _ftelli64(file);
    _fseeki64(file, 0, SEEK_SET);
    if (size < 0x200 || size > 64ll * 1024 * 1024) { fclose(file); return false; }
    db->bytes.resize(static_cast<size_t>(size));
    const bool ok = fread(db->bytes.data(), 1, db->bytes.size(), file) == db->bytes.size();
    fclose(file);
    if (!ok) return false;
    db->stored_checksum = be32(db->bytes.data());
    db->end_address = be32(db->bytes.data() + 4);
    return db->end_address == kLoadBase + db->bytes.size() - 1;
}

static void initialize(uint8_t state[256], const uint8_t* prefix, uint32_t d3) {
    const uint32_t seed_value = d3 ^ kSeedXor;
    const uint8_t seed[4] = {
        uint8_t(seed_value >> 24), uint8_t(seed_value >> 16),
        uint8_t(seed_value >> 8), uint8_t(seed_value)
    };
    for (unsigned i = 0; i < 256; ++i) state[i] = uint8_t(i);
    uint8_t j = 0;
    for (unsigned i = 0; i < 256; ++i) {
        const uint8_t key = uint8_t(prefix[i] ^ seed[i & 3] ^ uint8_t(i) ^ uint8_t(i * 4));
        j = uint8_t(j + state[i] + key);
        const uint8_t temp = state[i]; state[i] = state[j]; state[j] = temp;
    }
}

static uint8_t next_byte(uint8_t state[256], uint8_t* i, uint8_t* j) {
    *i = uint8_t(*i + 1);
    *j = uint8_t(*j + state[*i]);
    const uint8_t temp = state[*i]; state[*i] = state[*j]; state[*j] = temp;
    return state[uint8_t(state[*i] + state[*j])];
}

static bool plausible_vector(uint32_t value, uint32_t end_address) {
    if (value == 0 || value == 0xFFFFFFFFu) return true;
    return (value & 1u) == 0 && value >= kLoadBase && value <= end_address;
}

static bool vector_filter(const Database& db, uint32_t d3) {
    uint8_t state[256];
    initialize(state, db.bytes.data(), d3);
    uint8_t i = 0, j = 0, plain[8];
    for (unsigned n = 0; n < 8; ++n)
        plain[n] = uint8_t(db.bytes[kRawPrefix + n] ^ next_byte(state, &i, &j));
    return plausible_vector(be32(plain), db.end_address) &&
           plausible_vector(be32(plain + 4), db.end_address);
}

static bool native_checksum_matches(const Database& db, uint32_t d3) {
    uint8_t state[256];
    initialize(state, db.bytes.data(), d3);
    uint8_t i = 0, j = 0;
    uint32_t sum = 0;
    for (size_t n = 4; n < kRawPrefix; ++n) sum += db.bytes[n];
    for (size_t n = kRawPrefix; n < db.bytes.size(); ++n)
        sum += uint8_t(db.bytes[n] ^ next_byte(state, &i, &j));
    return sum == db.stored_checksum;
}

int main(int argc, char** argv) {
    if (argc < 2 || argc > 5) {
        fprintf(stderr, "usage: recover_database_d3 DATABASE [START [COUNT [THREADS]]]\n");
        return 2;
    }
    Database db{};
    if (!load_database(argv[1], &db)) {
        fprintf(stderr, "database structural load failed\n");
        return 3;
    }
    const uint64_t start = argc >= 3 ? _strtoui64(argv[2], nullptr, 0) : 0;
    const uint64_t count = argc >= 4 ? _strtoui64(argv[3], nullptr, 0) : 0x100000000ull;
    unsigned threads = argc >= 5 ? unsigned(strtoul(argv[4], nullptr, 0))
                                 : std::thread::hardware_concurrency();
    if (threads == 0) threads = 1;
    if (start > 0xFFFFFFFFull || count == 0 || start + count > 0x100000000ull) {
        fprintf(stderr, "invalid candidate range\n");
        return 4;
    }

    constexpr uint64_t chunk_size = 0x10000;
    const uint64_t end = start + count;
    std::atomic<uint64_t> next{start}, tested{0}, filtered{0};
    std::atomic<bool> found{false};
    std::atomic<uint32_t> answer{0};
    const ULONGLONG began = GetTickCount64();

    auto worker = [&]() {
        while (!found.load(std::memory_order_relaxed)) {
            const uint64_t first = next.fetch_add(chunk_size, std::memory_order_relaxed);
            if (first >= end) break;
            const uint64_t last = (first + chunk_size < end) ? first + chunk_size : end;
            uint64_t local_tested = 0, local_filtered = 0;
            for (uint64_t value = first; value < last; ++value) {
                const uint32_t d3 = uint32_t(value);
                ++local_tested;
                if (!vector_filter(db, d3)) continue;
                ++local_filtered;
                if (native_checksum_matches(db, d3)) {
                    answer.store(d3, std::memory_order_relaxed);
                    found.store(true, std::memory_order_release);
                    break;
                }
            }
            tested.fetch_add(local_tested, std::memory_order_relaxed);
            filtered.fetch_add(local_filtered, std::memory_order_relaxed);
        }
    };

    std::vector<std::thread> pool;
    for (unsigned n = 0; n < threads; ++n) pool.emplace_back(worker);
    ULONGLONG last_report = began;
    while (true) {
        bool any_joinable = false;
        const uint64_t assigned = next.load(std::memory_order_relaxed);
        if (found.load(std::memory_order_acquire) || assigned >= end) break;
        any_joinable = true;
        Sleep(1000);
        const ULONGLONG now = GetTickCount64();
        // Frequent enough for the launcher's progress display.
        if (now - last_report >= 2000) {
            const uint64_t done = tested.load(std::memory_order_relaxed);
            const double seconds = double(now - began) / 1000.0;
            fprintf(stdout, "D3_PROGRESS tested=%llu rate=%.0f/s filtered=%llu\n",
                    static_cast<unsigned long long>(done), done / seconds,
                    static_cast<unsigned long long>(filtered.load()));
            fflush(stdout);
            last_report = now;
        }
        (void)any_joinable;
    }
    for (auto& thread : pool) thread.join();
    const double seconds = double(GetTickCount64() - began) / 1000.0;
    const uint64_t done = tested.load();
    fprintf(stdout, "D3_SCAN_DONE tested=%llu seconds=%.3f rate=%.0f/s filtered=%llu\n",
            static_cast<unsigned long long>(done), seconds, seconds ? done / seconds : 0,
            static_cast<unsigned long long>(filtered.load()));
    if (found.load()) {
        fprintf(stdout, "D3_FOUND=0x%08X\n", answer.load());
        return 0;
    }
    return 1;
}
