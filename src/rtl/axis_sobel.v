`timescale 1ns / 1ps

// Streaming 3x3 Sobel filter for packed 8-bit grayscale pixels.
//
// The AXI stream is 32 bits wide and contains four pixels per word, with the
// earliest pixel in bits [7:0]. IMAGE_WIDTH is fixed by the hardware build;
// frame height is determined by the input TLAST. The output has the same byte
// count as the input. Its one-pixel border is zero.
module axis_sobel #(
    parameter integer IMAGE_WIDTH = 512
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
    output reg  [31:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TKEEP" *)
    output reg  [3:0]  m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TLAST" *)
    output reg         m_axis_tlast,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TVALID" *)
    output reg         m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS TREADY" *)
    input  wire        m_axis_tready
);
    localparam integer COL_BITS = $clog2(IMAGE_WIDTH);
    localparam integer FLUSH_BITS = $clog2(IMAGE_WIDTH + 2);
    localparam STATE_INPUT = 1'b0;
    localparam STATE_FLUSH = 1'b1;

    (* ram_style = "distributed" *) reg [7:0] line_bank0 [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_bank1 [0:IMAGE_WIDTH-1];

    reg state;
    reg [31:0] row;
    reg [COL_BITS-1:0] column;

    reg [31:0] input_word;
    reg [3:0] input_keep;
    reg input_last;
    reg [1:0] input_lane;
    reg have_input_word;

    reg [7:0] top_minus2, top_minus1;
    reg [7:0] middle_minus2, middle_minus1;
    reg [7:0] bottom_minus2, bottom_minus1;

    reg signed [10:0] gx_reg;
    reg signed [10:0] gy_reg;
    reg gradient_is_interior;
    reg gradient_valid;
    reg [7:0] filtered_pixel_reg;
    reg filtered_pixel_valid;
    reg [23:0] output_pack;
    reg [1:0] output_pack_count;
    reg [FLUSH_BITS-1:0] flush_remaining;

    wire [7:0] selected_input_pixel =
        (input_lane == 2'd0) ? input_word[7:0] :
        (input_lane == 2'd1) ? input_word[15:8] :
        (input_lane == 2'd2) ? input_word[23:16] : input_word[31:24];
    wire selected_input_keep = input_keep[input_lane];
    wire [7:0] current_pixel = selected_input_keep
                               ? selected_input_pixel : 8'd0;

    // Even rows overwrite bank 0 and odd rows overwrite bank 1. The value is
    // read before the same location is replaced, preserving the row r-2 pixel.
    wire [7:0] previous_row_pixel = row[0]
                                    ? line_bank0[column]
                                    : line_bank1[column];
    wire [7:0] older_row_pixel = row[0]
                                 ? line_bank1[column]
                                 : line_bank0[column];

    wire signed [10:0] sobel_gx;
    wire signed [10:0] sobel_gy;
    sobel_pixel sobel_pixel_i (
        .p00(top_minus2),     .p01(top_minus1),     .p02(older_row_pixel),
        .p10(middle_minus2),  .p11(middle_minus1),  .p12(previous_row_pixel),
        .p20(bottom_minus2),  .p21(bottom_minus1),  .p22(current_pixel),
        .gx(sobel_gx), .gy(sobel_gy), .magnitude()
    );

    // At input (r,c), the available 3x3 window produces output (r-1,c-1).
    // Stream output begins at input index IMAGE_WIDTH+1. The delayed pixels at
    // the end are all border zeros and are emitted in STATE_FLUSH.
    wire output_active = (row > 32'd1)
                       || ((row == 32'd1) && (column >= {{(COL_BITS-1){1'b0}}, 1'b1}));
    wire output_is_interior = (row >= 32'd2)
                            && (column >= {{(COL_BITS-2){1'b0}}, 2'b10});
    wire [10:0] abs_gx_reg = gx_reg[10] ? -gx_reg : gx_reg;
    wire [10:0] abs_gy_reg = gy_reg[10] ? -gy_reg : gy_reg;
    wire [11:0] magnitude_sum_reg = {1'b0, abs_gx_reg}
                                    + {1'b0, abs_gy_reg};
    wire [7:0] pipelined_magnitude = (magnitude_sum_reg > 12'd255)
                                   ? 8'hff : magnitude_sum_reg[7:0];
    wire [7:0] filtered_pixel = gradient_is_interior
                              ? pipelined_magnitude : 8'd0;

    wire output_register_available = ~m_axis_tvalid | m_axis_tready;
    wire can_append_output = (output_pack_count != 2'd3)
                           || output_register_available;
    wire filtered_pixel_ready = ~filtered_pixel_valid || can_append_output;
    wire gradient_ready = ~gradient_valid || filtered_pixel_ready;
    wire can_process_input = have_input_word
                           && (~output_active || gradient_ready);
    wire can_advance_gradient = gradient_valid && filtered_pixel_ready;
    wire can_append_filtered_pixel = filtered_pixel_valid
                                   && can_append_output;
    wire can_process_flush = (state == STATE_FLUSH)
                           && ~gradient_valid
                           && ~filtered_pixel_valid
                           && can_append_output;

    assign s_axis_tready = (state == STATE_INPUT) && ~have_input_word;

    always @(posedge axis_aclk) begin
        if (!axis_aresetn) begin
            state               <= STATE_INPUT;
            row                 <= 32'd0;
            column              <= {COL_BITS{1'b0}};
            input_word          <= 32'd0;
            input_keep          <= 4'd0;
            input_last          <= 1'b0;
            input_lane          <= 2'd0;
            have_input_word     <= 1'b0;
            top_minus2          <= 8'd0;
            top_minus1          <= 8'd0;
            middle_minus2       <= 8'd0;
            middle_minus1       <= 8'd0;
            bottom_minus2       <= 8'd0;
            bottom_minus1       <= 8'd0;
            gx_reg              <= 11'sd0;
            gy_reg              <= 11'sd0;
            gradient_is_interior <= 1'b0;
            gradient_valid      <= 1'b0;
            filtered_pixel_reg  <= 8'd0;
            filtered_pixel_valid <= 1'b0;
            output_pack         <= 24'd0;
            output_pack_count   <= 2'd0;
            flush_remaining     <= {FLUSH_BITS{1'b0}};
            m_axis_tdata        <= 32'd0;
            m_axis_tkeep        <= 4'd0;
            m_axis_tlast        <= 1'b0;
            m_axis_tvalid       <= 1'b0;
        end else begin
            if (m_axis_tvalid && m_axis_tready) begin
                m_axis_tvalid <= 1'b0;
                m_axis_tlast  <= 1'b0;
            end

            if (s_axis_tvalid && s_axis_tready) begin
                input_word      <= s_axis_tdata;
                input_keep      <= s_axis_tkeep;
                input_last      <= s_axis_tlast;
                input_lane      <= 2'd0;
                have_input_word <= 1'b1;
            end

            // Stage 2: magnitude and saturation. This stage stalls together
            // with the byte packer when the AXI output applies backpressure.
            if (can_advance_gradient) begin
                gradient_valid       <= 1'b0;
                filtered_pixel_reg   <= filtered_pixel;
                filtered_pixel_valid <= 1'b1;
            end

            // This register breaks the Sobel arithmetic from the byte packer.
            // It can be consumed and replaced in the same clock cycle.
            if (can_append_filtered_pixel) begin
                if (!can_advance_gradient)
                    filtered_pixel_valid <= 1'b0;
                if (output_pack_count == 2'd3) begin
                    m_axis_tdata      <= {filtered_pixel_reg, output_pack};
                    m_axis_tkeep      <= 4'hf;
                    m_axis_tlast      <= 1'b0;
                    m_axis_tvalid     <= 1'b1;
                    output_pack_count <= 2'd0;
                end else begin
                    if (output_pack_count == 2'd0)
                        output_pack[7:0] <= filtered_pixel_reg;
                    else if (output_pack_count == 2'd1)
                        output_pack[15:8] <= filtered_pixel_reg;
                    else
                        output_pack[23:16] <= filtered_pixel_reg;
                    output_pack_count <= output_pack_count + 1'b1;
                end
            end

            if ((state == STATE_INPUT) && can_process_input) begin
                if (row[0])
                    line_bank1[column] <= current_pixel;
                else
                    line_bank0[column] <= current_pixel;

                if (output_active) begin
                    gx_reg               <= sobel_gx;
                    gy_reg               <= sobel_gy;
                    gradient_is_interior <= output_is_interior;
                    gradient_valid       <= 1'b1;
                end

                if (column == IMAGE_WIDTH - 1) begin
                    column        <= {COL_BITS{1'b0}};
                    row           <= row + 1'b1;
                    top_minus2    <= 8'd0;
                    top_minus1    <= 8'd0;
                    middle_minus2 <= 8'd0;
                    middle_minus1 <= 8'd0;
                    bottom_minus2 <= 8'd0;
                    bottom_minus1 <= 8'd0;
                end else begin
                    column        <= column + 1'b1;
                    top_minus2    <= top_minus1;
                    top_minus1    <= older_row_pixel;
                    middle_minus2 <= middle_minus1;
                    middle_minus1 <= previous_row_pixel;
                    bottom_minus2 <= bottom_minus1;
                    bottom_minus1 <= current_pixel;
                end

                if (input_lane == 2'd3) begin
                    have_input_word <= 1'b0;
                    input_lane      <= 2'd0;
                    if (input_last) begin
                        state           <= STATE_FLUSH;
                        flush_remaining <= IMAGE_WIDTH + 1;
                    end
                end else begin
                    input_lane <= input_lane + 1'b1;
                end
            end

            if (can_process_flush) begin
                if (output_pack_count == 2'd3) begin
                    m_axis_tdata      <= {8'd0, output_pack};
                    m_axis_tkeep      <= 4'hf;
                    m_axis_tlast      <= (flush_remaining == 1);
                    m_axis_tvalid     <= 1'b1;
                    output_pack_count <= 2'd0;
                end else begin
                    if (output_pack_count == 2'd0)
                        output_pack[7:0] <= 8'd0;
                    else if (output_pack_count == 2'd1)
                        output_pack[15:8] <= 8'd0;
                    else
                        output_pack[23:16] <= 8'd0;
                    output_pack_count <= output_pack_count + 1'b1;
                end

                flush_remaining <= flush_remaining - 1'b1;
                if (flush_remaining == 1) begin
                    state           <= STATE_INPUT;
                    row             <= 32'd0;
                    column          <= {COL_BITS{1'b0}};
                    top_minus2      <= 8'd0;
                    top_minus1      <= 8'd0;
                    middle_minus2   <= 8'd0;
                    middle_minus1   <= 8'd0;
                    bottom_minus2   <= 8'd0;
                    bottom_minus1   <= 8'd0;
                end
            end
        end
    end
endmodule
