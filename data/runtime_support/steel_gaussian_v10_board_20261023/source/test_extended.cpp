#include "gaussian_row_q30.hpp"
#include "coefficients_q30.hpp"
#include <vector>
#include <cstdint>
#include <iostream>

static uint32_t random_state = 0x24681023;
static uint32_t pixel() {
    random_state ^= random_state << 13;
    random_state ^= random_state >> 17;
    random_state ^= random_state << 5;
    return random_state & 0x0fffffff;
}
int main() {
    unsigned cases = 0;
    for (unsigned mode = 0; mode < 4; ++mode) {
        const unsigned taps = TAPS_Q30[mode], width = 512 + taps - 1;
        for (unsigned rows : {1u, 512u}) {
            hls::stream<axis32> input, output;
            std::vector<uint32_t> data(rows * width);
            for (unsigned i = 0; i < data.size(); ++i) {
                // Mix full-range Q20 values and extreme carry propagation.
                data[i] = i % 4 == 0 ? 0x0fffffff : pixel();
                axis32 beat;
                beat.data = data[i]; beat.keep = 15; beat.strb = 15;
                beat.last = i + 1 == data.size(); input.write(beat);
            }
            unsigned status = 99;
            gaussian_row_q30(input, output, mode, rows, status);
            if (status != 0 || !input.empty() || output.size() != rows * 512) return 1;
            for (unsigned y = 0; y < rows; ++y)
                for (unsigned x = 0; x < 512; ++x) {
                    // Independent direct full-tap uint64 reference, no CSA.
                    uint64_t sum = uint64_t(1) << 29;
                    for (unsigned k = 0; k < taps; ++k) {
                        unsigned ck = k < (taps + 1) / 2 ? k : taps - 1 - k;
                        sum += uint64_t(data[y * width + x + k]) * COEFFICIENTS_Q30[mode][ck];
                    }
                    axis32 beat = output.read();
                    if (beat.data != (sum >> 30) || beat.keep != 15 || beat.strb != 15 ||
                        bool(beat.last) != (y + 1 == rows && x == 511)) return 2;
                }
            ++cases;
            std::cout << "EXTENDED mode=" << mode << " rows=" << rows << " EXACT\n";
        }
    }
    for (unsigned fault = 0; fault < 5; ++fault) {
        hls::stream<axis32> input, output;
        unsigned status = 99;
        for (unsigned i = 0; i < 520; ++i) {
            axis32 b; b.data = fault == 4 ? 0x10000000u : 0u;
            b.keep = fault == 2 ? 7 : 15; b.strb = fault == 3 ? 7 : 15;
            b.last = fault == 0 ? i == 0 : fault == 1 ? false : i == 519;
            input.write(b);
        }
        gaussian_row_q30(input, output, 0, 1, status);
        const unsigned expected_status = fault < 2 ? 2 : fault < 4 ? 4 : 8;
        if (status != expected_status || !input.empty() || output.size() != 512) return 3;
        for (unsigned i = 0; i < 512; ++i) {
            axis32 b = output.read();
            if (b.data != 0 || bool(b.last) != (i == 511)) return 4;
        }
        ++cases;
    }
    hls::stream<axis32> input, output;
    unsigned status;
    gaussian_row_q30(input, output, 0, 513, status);
    if (status != 1 || !input.empty() || !output.empty()) return 5;
    ++cases;
    std::cout << "EXTENDED_CSIM_PASS cases=" << cases << std::endl;
    return 0;
}
