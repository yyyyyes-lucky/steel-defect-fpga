`timescale 1ns / 1ps

// 32-bit DMA input -> four-pixel unpacker -> reflect101-padded interior
// window core -> one 32-bit Q8 result beat per original image pixel.
// The PS must pad the image by six reflect101 pixels on every side, so the
// input dimensions are IMAGE_WIDTH+12 by IMAGE_HEIGHT+12.
module axis_q8_padded_frame_chain_32to32 #(
    parameter integer IMAGE_WIDTH  = 512,
    parameter integer IMAGE_HEIGHT = 512
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 axis_aclk CLK",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME axis_aclk, ASSOCIATED_BUSIF S_AXIS:M_AXIS, ASSOCIATED_RESET axis_aresetn" *)
    input  wire        axis_aclk,

    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 axis_aresetn RST",
       X_INTERFACE_PARAMETER = "XIL_INTERFACENAME axis_aresetn, POLARITY ACTIVE_LOW" *)
    input  wire        axis_aresetn,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    input  wire [31:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TKEEP" *)
    input  wire [3:0]  s_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input  wire        s_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input  wire        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output wire        s_axis_tready,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TDATA" *)
    output wire [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *)
    output wire [3:0]  m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output wire        m_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output wire        m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input  wire        m_axis_tready
);

    localparam integer PADDED_WIDTH  = IMAGE_WIDTH + 12;
    localparam integer PADDED_HEIGHT = IMAGE_HEIGHT + 12;

    wire [7:0] unpack_data;
    wire       unpack_keep;
    wire       unpack_last;
    wire       unpack_valid;
    wire       unpack_ready;

    axis_gray_unpacker_4to1 unpacker (
        .axis_aclk(axis_aclk),
        .axis_aresetn(axis_aresetn),
        .s_axis_tdata(s_axis_tdata),
        .s_axis_tkeep(s_axis_tkeep),
        .s_axis_tlast(s_axis_tlast),
        .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready),
        .m_axis_tdata(unpack_data),
        .m_axis_tkeep(unpack_keep),
        .m_axis_tlast(unpack_last),
        .m_axis_tvalid(unpack_valid),
        .m_axis_tready(unpack_ready)
    );

    axis_local_std_q8_interior_1px #(
        .IMAGE_WIDTH(PADDED_WIDTH),
        .IMAGE_HEIGHT(PADDED_HEIGHT)
    ) window_core (
        .axis_aclk(axis_aclk),
        .axis_aresetn(axis_aresetn),
        .s_axis_tdata(unpack_data),
        .s_axis_tkeep(unpack_keep),
        .s_axis_tlast(unpack_last),
        .s_axis_tvalid(unpack_valid),
        .s_axis_tready(unpack_ready),
        .m_axis_tdata(m_axis_tdata),
        .m_axis_tkeep(m_axis_tkeep),
        .m_axis_tlast(m_axis_tlast),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready)
    );

endmodule
