#pragma once
#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>
typedef ap_axiu<32,0,0,0> axis32;
void blur3(hls::stream<axis32>& input, hls::stream<axis32>& output,
           unsigned height, unsigned width, unsigned& status);
