#include "ellipse7.hpp"

void ellipse7(hls::stream<axis32>& input, hls::stream<axis32>& output,
              unsigned height, unsigned width, unsigned& status) {
#pragma HLS INTERFACE axis port=input
#pragma HLS INTERFACE axis port=output
#pragma HLS INTERFACE s_axilite port=height bundle=control
#pragma HLS INTERFACE s_axilite port=width bundle=control
#pragma HLS INTERFACE s_axilite port=status bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    status = 0;
    if (height == 0 || height > 512 || width == 0 || width > 512) {
        status = 1;
        return;
    }
    ap_uint<1> rows[6][518];
#pragma HLS ARRAY_PARTITION variable=rows complete dim=1
#pragma HLS BIND_STORAGE variable=rows type=ram_s2p impl=bram
    ap_uint<1> window[7][7];
#pragma HLS ARRAY_PARTITION variable=window complete dim=0
scan_y:
    for (unsigned y = 0; y < height+6; ++y) {
    scan_x:
        for (unsigned x = 0; x < width+6; ++x) {
#pragma HLS PIPELINE II=1
            axis32 beat = input.read();
            if (bool(beat.last) != (y == height+5 && x == width+5)) status |= 2;
            if (beat.keep != 15 || beat.strb != 15) status |= 4;
            if (beat.data.range(31,1) != 0) status |= 8;
            const bool border = y < 3 || y >= height+3 || x < 3 || x >= width+3;
            if (border && beat.data != 0) status |= 16;
            ap_uint<1> column[7];
#pragma HLS ARRAY_PARTITION variable=column complete
            for (unsigned k = 0; k < 6; ++k) {
#pragma HLS UNROLL
                column[k] = rows[k][x];
            }
            column[6] = beat.data[0];
            for (unsigned k = 0; k < 6; ++k) {
#pragma HLS UNROLL
                rows[k][x] = column[k+1];
            }
            for (unsigned r = 0; r < 7; ++r) {
#pragma HLS UNROLL
                for (unsigned c = 0; c < 6; ++c) {
#pragma HLS UNROLL
                    window[r][c] = window[r][c+1];
                }
                window[r][6] = column[r];
            }
            if (y >= 6 && x >= 6) {
                ap_uint<1> value = 0;
                for (unsigned r = 0; r < 7; ++r) {
#pragma HLS UNROLL
                    for (unsigned c = 0; c < 7; ++c) {
#pragma HLS UNROLL
                        const bool active = (r == 0 || r == 6) ? c == 3 :
                                            (r == 1 || r == 5) ? (c >= 1 && c <= 5) : true;
                        if (active) value |= window[r][c];
                    }
                }
                axis32 result;
                result.data = value;
                result.keep = 15;
                result.strb = 15;
                result.last = y == height+5 && x == width+5;
                output.write(result);
            }
        }
    }
}
