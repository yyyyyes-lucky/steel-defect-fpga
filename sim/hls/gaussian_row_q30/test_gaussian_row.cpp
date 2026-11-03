#include "gaussian_row_q30.hpp"
#include <cstdint>
#include <fstream>
#include <iostream>

int main() {
    std::ifstream f("fixtures.bin", std::ios::binary);
    if (!f) return 1;
    uint32_t cases = 0;
    f.read(reinterpret_cast<char*>(&cases), sizeof(cases));
    for (unsigned c = 0; c < cases; ++c) {
        uint32_t mode, rows, width;
        f.read(reinterpret_cast<char*>(&mode), 4);
        f.read(reinterpret_cast<char*>(&rows), 4);
        f.read(reinterpret_cast<char*>(&width), 4);
        hls::stream<axis32> input, output;
        for (unsigned i = 0; i < rows * width; ++i) {
            uint32_t value;
            f.read(reinterpret_cast<char*>(&value), 4);
            axis32 beat;
            beat.data = value; beat.keep = 15; beat.strb = 15;
            beat.last = i + 1 == rows * width;
            input.write(beat);
        }
        unsigned status = 99;
        gaussian_row_q30(input, output, mode, rows, status);
        if (status != 0 || !input.empty()) return 2;
        for (unsigned i = 0; i < rows * 512; ++i) {
            uint32_t expected;
            f.read(reinterpret_cast<char*>(&expected), 4);
            if (output.empty()) return 3;
            axis32 actual = output.read();
            if (actual.data != expected || actual.keep != 15 || actual.strb != 15 ||
                bool(actual.last) != (i + 1 == rows * 512)) {
                std::cerr << "Mismatch case=" << c << " pixel=" << i << " expected="
                          << expected << " actual=" << actual.data << std::endl;
                return 4;
            }
        }
        if (!output.empty() || !f.good()) return 5;
        std::cout << "CASE " << c << " MODE " << mode << " EXACT" << std::endl;
    }
    hls::stream<axis32> input, output;
    unsigned status;
    gaussian_row_q30(input, output, 4, 1, status);
    if (status != 1 || !input.empty() || !output.empty()) return 6;
    gaussian_row_q30(input, output, 0, 0, status);
    if (status != 1) return 7;
    // Sideband error reporting must not stop mid-frame and strand a DMA.
    for (unsigned i = 0; i < 520; ++i) {
        axis32 beat;
        beat.data = 0x10000000; beat.keep = 7; beat.strb = 15; beat.last = 0;
        input.write(beat);
    }
    gaussian_row_q30(input, output, 0, 1, status);
    if (status != 14 || !input.empty() || output.size() != 512) return 8;
    for (unsigned i = 0; i < 512; ++i) {
        axis32 beat = output.read();
        if (beat.data != 0 || bool(beat.last) != (i == 511)) return 9;
    }
    std::cout << "ALL INTEGER OUTPUTS AND FRAME PROTOCOL CHECKS PASSED" << std::endl;
    return 0;
}
