`timescale 1ns / 1ps

// Converts one 32-bit AXI beat containing up to four 8-bit pixels into
// one 8-bit AXI beat per pixel. Lane 0 is s_axis_tdata[7:0].
module axis_gray_unpacker_4to1 (
    input  wire        axis_aclk,
    input  wire        axis_aresetn,

    input  wire [31:0] s_axis_tdata,
    input  wire [3:0]  s_axis_tkeep,
    input  wire        s_axis_tlast,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,

    output wire [7:0]  m_axis_tdata,
    output wire        m_axis_tkeep,
    output wire        m_axis_tlast,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready
);

    reg        busy;
    reg [1:0]  lane;
    reg [3:0]  keep_reg;
    reg        last_reg;
    reg [31:0] data_reg;

    wire [1:0] last_lane = keep_reg[3] ? 2'd3 :
                            keep_reg[2] ? 2'd2 :
                            keep_reg[1] ? 2'd1 : 2'd0;

    assign s_axis_tready = ~busy;
    assign m_axis_tvalid = busy;
    assign m_axis_tkeep  = busy;
    assign m_axis_tlast  = busy && last_reg && (lane == last_lane);
    assign m_axis_tdata  = (lane == 2'd0) ? data_reg[7:0] :
                           (lane == 2'd1) ? data_reg[15:8] :
                           (lane == 2'd2) ? data_reg[23:16] :
                                            data_reg[31:24];

    always @(posedge axis_aclk) begin
        if (!axis_aresetn) begin
            busy     <= 1'b0;
            lane     <= 2'd0;
            keep_reg <= 4'd0;
            last_reg <= 1'b0;
            data_reg <= 32'd0;
        end else begin
            if (!busy && s_axis_tvalid && s_axis_tready) begin
                busy     <= (s_axis_tkeep != 4'd0);
                lane     <= 2'd0;
                keep_reg <= s_axis_tkeep;
                last_reg <= s_axis_tlast;
                data_reg <= s_axis_tdata;
            end else if (busy && m_axis_tready) begin
                if (lane == last_lane)
                    busy <= 1'b0;
                else
                    lane <= lane + 1'b1;
            end
        end
    end

endmodule
