`timescale 1ns / 1ps

// One Sobel output pixel. The window generator supplies zero-valued borders.
module sobel_pixel (
    input  wire [7:0] p00,
    input  wire [7:0] p01,
    input  wire [7:0] p02,
    input  wire [7:0] p10,
    input  wire [7:0] p11,
    input  wire [7:0] p12,
    input  wire [7:0] p20,
    input  wire [7:0] p21,
    input  wire [7:0] p22,
    output wire signed [10:0] gx,
    output wire signed [10:0] gy,
    output wire [7:0] magnitude
);
    wire signed [10:0] p00_s = {3'b000, p00};
    wire signed [10:0] p01_s = {3'b000, p01};
    wire signed [10:0] p02_s = {3'b000, p02};
    wire signed [10:0] p10_s = {3'b000, p10};
    wire signed [10:0] p12_s = {3'b000, p12};
    wire signed [10:0] p20_s = {3'b000, p20};
    wire signed [10:0] p21_s = {3'b000, p21};
    wire signed [10:0] p22_s = {3'b000, p22};

    assign gx =
        -p00_s + p02_s
        - (p10_s <<< 1) + (p12_s <<< 1)
        - p20_s + p22_s;
    assign gy =
        -p00_s - (p01_s <<< 1) - p02_s
        + p20_s + (p21_s <<< 1) + p22_s;

    wire [10:0] abs_gx = gx[10] ? -gx : gx;
    wire [10:0] abs_gy = gy[10] ? -gy : gy;
    wire [11:0] magnitude_sum = {1'b0, abs_gx} + {1'b0, abs_gy};

    assign magnitude = (magnitude_sum > 12'd255)
                     ? 8'hff
                     : magnitude_sum[7:0];
endmodule
