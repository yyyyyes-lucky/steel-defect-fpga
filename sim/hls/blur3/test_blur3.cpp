#include "blur3.hpp"
#include <cstdint>
#include <fstream>
#include <iostream>
#include <vector>

int main() {
    std::ifstream f("fixtures.bin",std::ios::binary);
    if (!f) return 1;
    uint32_t cases=0;
    f.read(reinterpret_cast<char*>(&cases),4);
    for (unsigned trial=0;trial<cases;++trial) {
        uint32_t h,w,n;
        f.read(reinterpret_cast<char*>(&h),4);
        f.read(reinterpret_cast<char*>(&w),4);
        f.read(reinterpret_cast<char*>(&n),4);
        hls::stream<axis32> input,output;
        for (unsigned i=0;i<n;i+=4) {
            uint32_t word=0;f.read(reinterpret_cast<char*>(&word),4);
            axis32 b;b.data=word;b.keep=15;b.strb=15;b.last=i+4==n;
            input.write(b);
        }
        std::vector<unsigned char> expected(h*w);
        f.read(reinterpret_cast<char*>(expected.data()),h*w);
        if (!f.good()) return 2;
        unsigned status=99;
        blur3(input,output,h,w,status);
        if (status || !input.empty()) return 3;
        for (unsigned i=0;i<h*w;i+=4) {
            if (output.empty()) return 4;
            axis32 b=output.read();
            unsigned bytes=(h*w-i<4)?h*w-i:4;
            if (b.keep!=((1u<<bytes)-1) || b.strb!=b.keep ||
                bool(b.last)!=(i+bytes==h*w)) return 5;
            for (unsigned j=0;j<bytes;++j) {
                if (b.data.range(j*8+7,j*8)!=expected[i+j]) {
                    std::cerr<<"Mismatch case "<<trial<<" pixel "<<i+j<<std::endl;
                    return 6;
                }
            }
        }
        if (!output.empty()) return 7;
        std::cout<<"BLUR3_CASE "<<trial<<" "<<h<<"x"<<w<<" EXACT"<<std::endl;
    }
    hls::stream<axis32> input,output;unsigned status;
    blur3(input,output,0,1,status);
    if (status!=1 || !input.empty() || !output.empty()) return 8;
    blur3(input,output,1,513,status);
    if (status!=1) return 9;
    // Invalid TLAST, byte masks and nonzero transport-only padding.
    for (unsigned i=0;i<364;i+=4) {
        axis32 b;b.data=0xffffffff;b.keep=7;b.strb=15;b.last=0;input.write(b);
    }
    blur3(input,output,1,1,status);
    if (status!=14 || !input.empty() || output.size()!=1) return 10;
    axis32 bad=output.read();
    if (bad.data.range(7,0)!=255 || bad.keep!=1 || !bad.last) return 11;
    // Recover shape/state after the malformed frame, without resetting.
    for (unsigned i=0;i<364;i+=4) {
        axis32 b;b.data=0;b.keep=15;b.strb=15;b.last=i==360;input.write(b);
    }
    blur3(input,output,1,1,status);
    if (status || !input.empty() || output.size()!=1 || output.read().data!=0) return 12;
    std::cout<<"BLUR3_INTEGER_AND_FRAME_PROTOCOL_PASS cases="<<cases<<std::endl;
    return 0;
}
