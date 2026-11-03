#include "gaussian_row_q30.hpp"
#include "coefficients_q30.hpp"

// Same arithmetic and external protocol as v9. Only the scheduling changes:
// keep one pipeline active across consecutive pixels instead of draining it
// after every pixel's group loop.
void gaussian_row_q30(hls::stream<axis32>& input,
                      hls::stream<axis32>& output,
                      unsigned kernel_id, unsigned rows, unsigned& status) {
#pragma HLS INTERFACE axis port=input
#pragma HLS INTERFACE axis port=output
#pragma HLS INTERFACE s_axilite port=kernel_id bundle=control
#pragma HLS INTERFACE s_axilite port=rows bundle=control
#pragma HLS INTERFACE s_axilite port=status bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    status = 0;
    if (kernel_id > 3 || rows == 0 || rows > 512) {
        status = 1;
        return;
    }
    const unsigned taps = TAPS_Q30[kernel_id];
    const unsigned padded_width = 512 + taps - 1;
    const unsigned pairs = (taps + 1) / 2;
    const unsigned groups = (pairs + 7) / 8;
    ap_uint<28> row[712];
#pragma HLS ARRAY_PARTITION variable=row cyclic factor=16 dim=1
#pragma HLS BIND_STORAGE variable=row type=ram_t2p impl=bram
    ap_uint<30> coefficients[104];
#pragma HLS ARRAY_PARTITION variable=coefficients cyclic factor=8 dim=1
load_coefficients:
    for (unsigned i = 0; i < 104; ++i) {
#pragma HLS PIPELINE II=1
        coefficients[i] = COEFFICIENTS_Q30[kernel_id][i];
    }
row_loop:
    for (unsigned y = 0; y < rows; ++y) {
#pragma HLS LOOP_TRIPCOUNT min=1 max=512
    load_row:
        for (unsigned x = 0; x < padded_width; ++x) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=520 max=712
            axis32 beat = input.read();
            row[x] = beat.data.range(27, 0);
            const bool expected_last = y + 1 == rows && x + 1 == padded_width;
            if (bool(beat.last) != expected_last) status |= 2;
            if (beat.keep != 15 || beat.strb != 15) status |= 4;
            if (beat.data.range(31, 28) != 0) status |= 8;
        }
        unsigned x = 0, group = 0;
        // Carry-save state removes a 60-bit carry chain from the II=1
        // feedback. Invariant: sum_bits + carry_bits is the exact partial sum.
        // Positive normalized coefficients bound the complete sum below 2^58.
        ap_uint<60> sum_bits = 0, carry_bits = 0;
    continuous_groups:
        for (unsigned iteration = 0; iteration < 512 * groups; ++iteration) {
#pragma HLS PIPELINE II=1
#pragma HLS LOOP_TRIPCOUNT min=512 max=6656
            ap_uint<60> products[8];
#pragma HLS ARRAY_PARTITION variable=products complete
            for (unsigned lane = 0; lane < 8; ++lane) {
#pragma HLS UNROLL
                const unsigned k = group * 8 + lane;
                ap_uint<29> pair = 0;
                if (k < pairs) {
                    pair = row[x + k];
                    if (2 * k + 1 != taps) pair += row[x + taps - 1 - k];
                }
                products[lane] = pair * coefficients[k];
            }
            ap_uint<60> partial =
                ((products[0] + products[1]) + (products[2] + products[3])) +
                ((products[4] + products[5]) + (products[6] + products[7]));
            ap_uint<60> a = group == 0 ? ap_uint<60>(0) : sum_bits;
            ap_uint<60> b = group == 0 ? ap_uint<60>(0) : carry_bits;
            ap_uint<60> next_bits = a ^ b ^ partial;
            ap_uint<60> next_carry = ((a & b) | (a & partial) | (b & partial)) << 1;
            sum_bits = next_bits;
            carry_bits = next_carry;
            if (group + 1 == groups) {
                axis32 beat;
                beat.data = (next_bits + next_carry + (ap_uint<60>(1) << 29)) >> 30;
                beat.keep = 15;
                beat.strb = 15;
                beat.last = y + 1 == rows && x == 511;
                output.write(beat);
                group = 0;
                ++x;
            } else {
                ++group;
            }
        }
    }
}
