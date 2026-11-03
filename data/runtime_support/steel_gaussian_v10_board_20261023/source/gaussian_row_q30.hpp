#pragma once
#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

typedef ap_axiu<32, 0, 0, 0> axis32;
// One horizontal pass only. PS supplies reflect101 padding and transposes for
// a vertical pass. Four passes form the two-stage dark-response approximation.
void gaussian_row_q30(hls::stream<axis32>& input,
                      hls::stream<axis32>& output,
                      unsigned kernel_id, unsigned rows, unsigned& status);
