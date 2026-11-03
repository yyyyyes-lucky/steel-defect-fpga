#include "ellipse7.hpp"
#include <cstdint>
#include <fstream>
#include <iostream>

int main() {
    std::ifstream f("fixtures.bin", std::ios::binary);
    if (!f) return 1;
    uint32_t cases;
    f.read(reinterpret_cast<char*>(&cases), 4);
    for (unsigned trial = 0; trial < cases; ++trial) {
        uint32_t height, width;
        f.read(reinterpret_cast<char*>(&height), 4);
        f.read(reinterpret_cast<char*>(&width), 4);
        hls::stream<axis32> input, output;
        unsigned words = (height+6)*(width+6);
        for (unsigned i = 0; i < words; ++i) {
            uint32_t value;
            f.read(reinterpret_cast<char*>(&value), 4);
            axis32 beat;
            beat.data = value; beat.keep = 15; beat.strb = 15;
            beat.last = i+1 == words;
            input.write(beat);
        }
        unsigned status = 99;
        ellipse7(input, output, height, width, status);
        if (status != 0 || !input.empty()) return 2;
        for (unsigned i = 0; i < height*width; ++i) {
            uint32_t expected;
            f.read(reinterpret_cast<char*>(&expected), 4);
            if (output.empty()) return 3;
            axis32 value = output.read();
            if (value.data != expected || value.keep != 15 || value.strb != 15 ||
                bool(value.last) != (i+1 == height*width)) {
                std::cerr << "Mismatch case " << trial << " pixel " << i << std::endl;
                return 4;
            }
        }
        if (!f.good() || !output.empty()) return 5;
        std::cout << "ELLIPSE_CASE " << trial << " " << height << "x" << width << " EXACT" << std::endl;
    }
    hls::stream<axis32> input, output;
    unsigned status;
    ellipse7(input, output, 0, 1, status);
    if (status != 1 || !input.empty() || !output.empty()) return 6;
    ellipse7(input, output, 1, 513, status);
    if (status != 1) return 7;
    // Consume a whole malformed 1x1 frame and report ALL protocol errors.
    for (unsigned i = 0; i < 49; ++i) {
        axis32 beat;
        beat.data = 3; beat.keep = 7; beat.strb = 15; beat.last = 0;
        input.write(beat);
    }
    ellipse7(input, output, 1, 1, status);
    if (status != 30 || !input.empty() || output.size() != 1) return 8;
    axis32 result = output.read();
    if (result.data != 1 || result.keep != 15 || result.strb != 15 || !result.last) return 9;
    // A valid empty frame after an error must have no stale foreground.
    for (unsigned i = 0; i < 49; ++i) {
        axis32 beat;
        beat.data = 0; beat.keep = 15; beat.strb = 15; beat.last = i == 48;
        input.write(beat);
    }
    ellipse7(input, output, 1, 1, status);
    if (status || !input.empty() || output.size() != 1 || output.read().data != 0) return 10;
    std::cout << "ELLIPSE_INTEGER_AND_FRAME_PROTOCOL_PASS" << std::endl;
    return 0;
}
