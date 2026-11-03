#include "blur3.hpp"

// Input: REFLECT_101 padded uint8 raster, (h+18)*(w+18) bytes,
// padded with zero bytes to a whole 32-bit beat. Little-endian pixel packing.
// Output: h*w uint8 pixels, final TKEEP/TSTRB marks the valid bytes.
// The PS performs ONLY border extension; both convolutions are in PL.
void blur3(hls::stream<axis32>& input, hls::stream<axis32>& output,
           unsigned height, unsigned width, unsigned& status) {
#pragma HLS INTERFACE axis port=input
#pragma HLS INTERFACE axis port=output
#pragma HLS INTERFACE s_axilite port=height bundle=control
#pragma HLS INTERFACE s_axilite port=width bundle=control
#pragma HLS INTERFACE s_axilite port=status bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    status=0;
    if (!height || height>512 || !width || width>512) {status=1; return;}
    const unsigned C[19]={0,1,3,4,9,14,20,28,32,34,32,28,20,14,9,4,3,1,0};
#pragma HLS ARRAY_PARTITION variable=C complete
    ap_uint<8> window[19];
#pragma HLS ARRAY_PARTITION variable=window complete
    ap_uint<16> rows[18][512];
#pragma HLS ARRAY_PARTITION variable=rows complete dim=1
#pragma HLS BIND_STORAGE variable=rows type=ram_s2p impl=bram
    ap_uint<32> packed_in=0, packed_out=0;
    unsigned position=0, out_position=0;
    const unsigned input_bytes=(height+18)*(width+18);
scan_y:
    for (unsigned y=0;y<height+18;++y) {
    scan_x:
        for (unsigned x=0;x<width+18;++x) {
#pragma HLS PIPELINE II=1
            if ((position&3)==0) {
                axis32 beat=input.read();
                packed_in=beat.data;
                if (bool(beat.last)!=(position+4>=input_bytes)) status|=2;
                if (beat.keep!=15 || beat.strb!=15) status|=4;
                if (position+4>input_bytes &&
                    (beat.data >> (8*(input_bytes-position)))!=0) status|=8;
            }
            ap_uint<8> pixel=packed_in.range(7,0);
            packed_in >>= 8;
            ++position;
            for (unsigned k=0;k<18;++k) {
#pragma HLS UNROLL
                window[k]=window[k+1];
            }
            window[18]=pixel;
            if (x>=18) {
                ap_uint<16> horizontal=0;
                for (unsigned k=0;k<19;++k) {
#pragma HLS UNROLL
                    horizontal += window[k]*C[k];
                }
                ap_uint<16> column[19];
#pragma HLS ARRAY_PARTITION variable=column complete
                for (unsigned k=0;k<18;++k) {
#pragma HLS UNROLL
                    column[k]=rows[k][x-18];
                }
                column[18]=horizontal;
                for (unsigned k=0;k<18;++k) {
#pragma HLS UNROLL
                    rows[k][x-18]=column[k+1];
                }
                if (y>=18) {
                    ap_uint<24> vertical=0;
                    for (unsigned k=0;k<19;++k) {
#pragma HLS UNROLL
                        vertical += column[k]*C[k];
                    }
                    ap_uint<8> value=(ap_uint<25>(vertical)+32768)>>16;
                    unsigned lane=out_position&3;
                    packed_out.range(lane*8+7,lane*8)=value;
                    ++out_position;
                    if (lane==3 || out_position==height*width) {
                        axis32 beat;
                        beat.data=packed_out;
                        beat.keep=(1u<<(lane+1))-1;
                        beat.strb=beat.keep;
                        beat.last=out_position==height*width;
                        output.write(beat);
                        packed_out=0;
                    }
                }
            }
        }
    }
}
