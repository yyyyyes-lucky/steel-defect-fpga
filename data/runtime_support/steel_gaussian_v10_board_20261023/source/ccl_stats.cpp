// Bounded host C++ model for a future row-streaming, 8-connected CCA core.
// NO RTL claim: no AXIS transport, scheduling or synthesis is implemented here.
// Stats only; this does not return labels, filtered masks, contours or geometry.
#include <cstdint>
#include <algorithm>

namespace {
constexpr unsigned RUNS = 256, NODES = 512;
struct Run { unsigned left, right, label; };
struct Workspace {
    Run previous[RUNS], current[RUNS];
    unsigned parent[NODES], area[NODES], rank[NODES], mapping[NODES];
    unsigned compact_area[RUNS];
    bool present[NODES];
};
struct Result {
    uint32_t count, pixels, largest, count_ge8, count_ge20;
    uint32_t peak_nodes, peak_runs, peak_find_depth, workspace_bytes;
};
unsigned root(Workspace& w, unsigned n, Result& r) {
    unsigned depth = 0;
    while (w.parent[n] != n) { n = w.parent[n]; ++depth; }
    r.peak_find_depth = std::max(r.peak_find_depth, depth);
    return n;
}
void merge(Workspace& w, unsigned a, unsigned b, Result& r) {
    a = root(w, a, r); b = root(w, b, r);
    if (a == b) return;
    if (w.rank[a] < w.rank[b]) std::swap(a, b);
    w.parent[b] = a;
    w.area[a] += w.area[b];
    if (w.rank[a] == w.rank[b]) ++w.rank[a];
}
}

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif
extern "C" EXPORT int ccl_stats(const uint8_t* mask, unsigned height,
                               unsigned width, uint32_t* result) {
    if (!mask || !result || !height || !width || height > 512 || width > 512) return 1;
    Workspace w{};
    Result r{};
    r.workspace_bytes = sizeof(w);
    unsigned previous_count = 0, active_count = 0;
    // Empty sentinel row retires every component at the end of the image.
    for (unsigned y = 0; y <= height; ++y) {
        unsigned nodes = active_count, current_count = 0;
        for (unsigned i = 0; i < nodes; ++i) {
            w.parent[i] = i; w.area[i] = w.compact_area[i]; w.rank[i] = 0;
        }
        unsigned x = 0, first = 0;
        while (y < height && x < width) {
            if (mask[y * width + x] == 0) { ++x; continue; }
            unsigned left = x;
            while (x < width && mask[y * width + x] != 0) ++x;
            unsigned right = x - 1;
            if (nodes >= NODES || current_count >= RUNS) return 2;
            unsigned label = nodes++;
            w.parent[label] = label; w.area[label] = right - left + 1; w.rank[label] = 0;
            w.current[current_count++] = {left, right, label};
            // Extend previous runs by one column for diagonal connectivity.
            while (first < previous_count && w.previous[first].right + 1 < left) ++first;
            for (unsigned j = first; j < previous_count && w.previous[j].left <= right + 1; ++j)
                merge(w, label, w.previous[j].label, r);
        }
        r.peak_nodes = std::max(r.peak_nodes, nodes);
        r.peak_runs = std::max(r.peak_runs, current_count);
        for (unsigned i = 0; i < nodes; ++i) {
            w.present[i] = false; w.mapping[i] = NODES;
        }
        for (unsigned i = 0; i < current_count; ++i) {
            unsigned label = root(w, w.current[i].label, r);
            w.current[i].label = label; w.present[label] = true;
        }
        for (unsigned i = 0; i < nodes; ++i) {
            if (w.parent[i] == i && !w.present[i]) {
                ++r.count; r.pixels += w.area[i];
                r.largest = std::max(r.largest, w.area[i]);
                r.count_ge8 += w.area[i] >= 8;
                r.count_ge20 += w.area[i] >= 20;
            }
        }
        active_count = 0;
        for (unsigned i = 0; i < current_count; ++i) {
            unsigned label = w.current[i].label;
            if (w.mapping[label] == NODES) {
                if (active_count >= RUNS) return 2;
                w.mapping[label] = active_count;
                w.compact_area[active_count++] = w.area[label];
            }
            w.previous[i] = {w.current[i].left, w.current[i].right, w.mapping[label]};
        }
        previous_count = current_count;
    }
    result[0] = r.count; result[1] = r.pixels; result[2] = r.largest;
    result[3] = r.count_ge8; result[4] = r.count_ge20;
    result[5] = r.peak_nodes; result[6] = r.peak_runs;
    result[7] = r.peak_find_depth; result[8] = r.workspace_bytes;
    return 0;
}
