`timescale 1ns / 1ps

// Intermediate verification core.
// One 8-bit pixel is accepted per AXI beat. It keeps the previous 12 rows
// and a 13-column shift register for each row, then emits the 7x7 and 13x13
// Q8 standard deviations for interior pixels only. Boundary reflection and
// the final 4-pixel/beat packer are intentionally handled in later stages.
module axis_local_std_q8_interior_1px #(
    parameter integer IMAGE_WIDTH  = 16,
    parameter integer IMAGE_HEIGHT = 16
) (
    input  wire        axis_aclk,
    input  wire        axis_aresetn,

    input  wire [7:0]  s_axis_tdata,
    input  wire        s_axis_tkeep,
    input  wire        s_axis_tlast,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,

    output wire [31:0] m_axis_tdata,
    output wire [3:0]  m_axis_tkeep,
    output wire        m_axis_tlast,
    output wire        m_axis_tvalid,
    input  wire         m_axis_tready
);

    // Twelve independent line-delay memories avoid the large bank-select mux
    // inferred by a dynamically indexed 2-D circular buffer. Asynchronous
    // reads deliberately target compact distributed RAM on 7-series devices.
    (* ram_style = "distributed" *) reg [7:0] line_mem0  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem1  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem2  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem3  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem4  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem5  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem6  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem7  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem8  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem9  [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem10 [0:IMAGE_WIDTH-1];
    (* ram_style = "distributed" *) reg [7:0] line_mem11 [0:IMAGE_WIDTH-1];
    reg [7:0] hshift   [0:12][0:12];

    reg [15:0] row_sum7 [0:12];
    reg [23:0] row_sumsq7 [0:12];
    reg [15:0] row_sum13 [0:12];
    reg [23:0] row_sumsq13 [0:12];
    reg [7:0] incoming_pixel [0:12];

    reg [15:0] x_count;
    reg [15:0] y_count;
    reg        sample_valid;
    reg        sample_x_zero;
    reg        sample_window;
    reg        sample_last;
    reg        pending_window;
    reg        pending_last;

    reg [7:0]  sample_in13 [0:12];
    reg [7:0]  sample_out13 [0:12];
    reg [15:0] sample_in13_sq [0:12];
    reg [15:0] sample_out13_sq [0:12];
    reg [7:0]  sample_in7 [0:12];
    reg [7:0]  sample_out7 [0:12];
    reg [15:0] sample_in7_sq [0:12];
    reg [15:0] sample_out7_sq [0:12];

    wire pipe_ce = ~m_axis_tvalid || m_axis_tready;
    wire input_fire = s_axis_tvalid && s_axis_tready && s_axis_tkeep;

    assign s_axis_tready = pipe_ce;

    // Maintain horizontal statistics as columns advance. The 7x7 window is
    // centered inside the 13x13 window and therefore slides independently.
    always @* begin
        incoming_pixel[0]  = s_axis_tdata;
        incoming_pixel[1]  = line_mem0[x_count];
        incoming_pixel[2]  = line_mem1[x_count];
        incoming_pixel[3]  = line_mem2[x_count];
        incoming_pixel[4]  = line_mem3[x_count];
        incoming_pixel[5]  = line_mem4[x_count];
        incoming_pixel[6]  = line_mem5[x_count];
        incoming_pixel[7]  = line_mem6[x_count];
        incoming_pixel[8]  = line_mem7[x_count];
        incoming_pixel[9]  = line_mem8[x_count];
        incoming_pixel[10] = line_mem9[x_count];
        incoming_pixel[11] = line_mem10[x_count];
        incoming_pixel[12] = line_mem11[x_count];
    end

    wire [15:0] std7_q8;
    wire [15:0] std13_q8;
    wire        std7_valid;
    wire        std13_valid;

    // Pipeline the vertical reductions so no stage contains more than two
    // levels of 24-bit addition. Parentheses alone are insufficient because
    // synthesis may reassociate a wide multi-operand expression.
    (* keep = "true" *) reg        reduce_valid1;
    (* keep = "true" *) reg        reduce_valid2;
    (* keep = "true" *) reg [15:0] sum7_pair [0:3];
    (* keep = "true" *) reg [23:0] sumsq7_pair [0:3];
    (* keep = "true" *) reg [15:0] sum13_pair [0:6];
    (* keep = "true" *) reg [23:0] sumsq13_pair [0:6];
    (* keep = "true" *) reg [15:0] sum7_group [0:1];
    (* keep = "true" *) reg [23:0] sumsq7_group [0:1];
    (* keep = "true" *) reg [15:0] sum13_group [0:3];
    (* keep = "true" *) reg [23:0] sumsq13_group [0:3];
    (* keep = "true" *) reg        stats_valid;
    (* keep = "true" *) reg [15:0] sum7_pipe;
    (* keep = "true" *) reg [23:0] sumsq7_pipe;
    (* keep = "true" *) reg [15:0] sum13_pipe;
    (* keep = "true" *) reg [23:0] sumsq13_pipe;

    q8_std_math_pipe #(.WINDOW_COUNT(49)) math7 (
        .clk(axis_aclk),
        .resetn(axis_aresetn),
        .ce(pipe_ce),
        .valid_in(stats_valid),
        .sum_in(sum7_pipe),
        .sumsq_in(sumsq7_pipe),
        .valid_out(std7_valid),
        .std_q8(std7_q8)
    );

    q8_std_math_pipe #(.WINDOW_COUNT(169)) math13 (
        .clk(axis_aclk),
        .resetn(axis_aresetn),
        .ce(pipe_ce),
        .valid_in(stats_valid),
        .sum_in(sum13_pipe),
        .sumsq_in(sumsq13_pipe),
        .valid_out(std13_valid),
        .std_q8(std13_q8)
    );

    reg [25:0] last_pipe;

    assign m_axis_tdata  = {std13_q8, std7_q8};
    assign m_axis_tkeep  = m_axis_tvalid ? 4'hf : 4'd0;
    assign m_axis_tlast  = m_axis_tvalid && last_pipe[25];
    assign m_axis_tvalid = std7_valid && std13_valid;

    integer i;
    integer j;

    always @(posedge axis_aclk) begin
        if (!axis_aresetn) begin
            x_count        <= 16'd0;
            y_count        <= 16'd0;
            sample_valid   <= 1'b0;
            sample_x_zero  <= 1'b0;
            sample_window  <= 1'b0;
            sample_last    <= 1'b0;
            pending_window <= 1'b0;
            pending_last   <= 1'b0;
            reduce_valid1  <= 1'b0;
            reduce_valid2  <= 1'b0;
            stats_valid    <= 1'b0;
            sum7_pipe      <= 16'd0;
            sumsq7_pipe    <= 24'd0;
            sum13_pipe     <= 16'd0;
            sumsq13_pipe   <= 24'd0;
            last_pipe      <= 26'd0;
            for (i = 0; i < 7; i = i + 1) begin
                sum13_pair[i]   <= 16'd0;
                sumsq13_pair[i] <= 24'd0;
            end
            for (i = 0; i < 4; i = i + 1) begin
                sum7_pair[i]    <= 16'd0;
                sumsq7_pair[i]  <= 24'd0;
                sum13_group[i]  <= 16'd0;
                sumsq13_group[i] <= 24'd0;
            end
            for (i = 0; i < 2; i = i + 1) begin
                sum7_group[i]   <= 16'd0;
                sumsq7_group[i] <= 24'd0;
            end
            for (i = 0; i < 13; i = i + 1) begin
                sample_in13[i]     <= 8'd0;
                sample_out13[i]    <= 8'd0;
                sample_in13_sq[i]  <= 16'd0;
                sample_out13_sq[i] <= 16'd0;
                sample_in7[i]      <= 8'd0;
                sample_out7[i]     <= 8'd0;
                sample_in7_sq[i]   <= 16'd0;
                sample_out7_sq[i]  <= 16'd0;
                row_sum7[i]    <= 16'd0;
                row_sumsq7[i]  <= 24'd0;
                row_sum13[i]   <= 16'd0;
                row_sumsq13[i] <= 24'd0;
                for (j = 0; j < 13; j = j + 1)
                    hshift[i][j] <= 8'd0;
            end
        end else begin
            if (pipe_ce) begin
                // Capture one pixel's row-update operands first. This breaks
                // the asynchronous line-RAM read and pixel square away from
                // the following 24-bit running-sum update.
                sample_valid <= input_fire;
                if (input_fire) begin
                    sample_x_zero <= (x_count == 16'd0);
                    sample_window <= (x_count >= 16'd12) &&
                                     (y_count >= 16'd12);
                    sample_last <= (x_count == IMAGE_WIDTH-1) &&
                                   (y_count == IMAGE_HEIGHT-1);
                    for (i = 0; i <= 12; i = i + 1) begin
                        sample_in13[i] <= incoming_pixel[i];
                        sample_out13[i] <= hshift[i][0];
                        sample_in13_sq[i] <= incoming_pixel[i] *
                                             incoming_pixel[i];
                        sample_out13_sq[i] <= hshift[i][0] *
                                              hshift[i][0];
                        sample_in7[i] <= hshift[i][10];
                        sample_out7[i] <= hshift[i][3];
                        sample_in7_sq[i] <= hshift[i][10] *
                                            hshift[i][10];
                        sample_out7_sq[i] <= hshift[i][3] *
                                             hshift[i][3];
                    end
                end

                pending_window <= sample_valid && sample_window;
                pending_last <= sample_valid && sample_last;
                if (sample_valid) begin
                    if (sample_x_zero) begin
                        for (i = 0; i <= 12; i = i + 1) begin
                            row_sum13[i] <= {8'd0, sample_in13[i]};
                            row_sumsq13[i] <= {8'd0, sample_in13_sq[i]};
                        end
                        for (i = 3; i <= 9; i = i + 1) begin
                            row_sum7[i] <= 16'd0;
                            row_sumsq7[i] <= 24'd0;
                        end
                    end else begin
                        for (i = 0; i <= 12; i = i + 1) begin
                            row_sum13[i] <= row_sum13[i] -
                                            sample_out13[i] +
                                            sample_in13[i];
                            row_sumsq13[i] <= row_sumsq13[i] -
                                              {8'd0, sample_out13_sq[i]} +
                                              {8'd0, sample_in13_sq[i]};
                        end
                        for (i = 3; i <= 9; i = i + 1) begin
                            row_sum7[i] <= row_sum7[i] - sample_out7[i] +
                                           sample_in7[i];
                            row_sumsq7[i] <= row_sumsq7[i] -
                                             {8'd0, sample_out7_sq[i]} +
                                             {8'd0, sample_in7_sq[i]};
                        end
                    end
                end

                reduce_valid1 <= pending_window;
                sum7_pair[0] <= row_sum7[3] + row_sum7[4];
                sum7_pair[1] <= row_sum7[5] + row_sum7[6];
                sum7_pair[2] <= row_sum7[7] + row_sum7[8];
                sum7_pair[3] <= row_sum7[9];
                sumsq7_pair[0] <= row_sumsq7[3] + row_sumsq7[4];
                sumsq7_pair[1] <= row_sumsq7[5] + row_sumsq7[6];
                sumsq7_pair[2] <= row_sumsq7[7] + row_sumsq7[8];
                sumsq7_pair[3] <= row_sumsq7[9];

                sum13_pair[0] <= row_sum13[0] + row_sum13[1];
                sum13_pair[1] <= row_sum13[2] + row_sum13[3];
                sum13_pair[2] <= row_sum13[4] + row_sum13[5];
                sum13_pair[3] <= row_sum13[6] + row_sum13[7];
                sum13_pair[4] <= row_sum13[8] + row_sum13[9];
                sum13_pair[5] <= row_sum13[10] + row_sum13[11];
                sum13_pair[6] <= row_sum13[12];
                sumsq13_pair[0] <= row_sumsq13[0] + row_sumsq13[1];
                sumsq13_pair[1] <= row_sumsq13[2] + row_sumsq13[3];
                sumsq13_pair[2] <= row_sumsq13[4] + row_sumsq13[5];
                sumsq13_pair[3] <= row_sumsq13[6] + row_sumsq13[7];
                sumsq13_pair[4] <= row_sumsq13[8] + row_sumsq13[9];
                sumsq13_pair[5] <= row_sumsq13[10] + row_sumsq13[11];
                sumsq13_pair[6] <= row_sumsq13[12];

                reduce_valid2 <= reduce_valid1;
                sum7_group[0] <= sum7_pair[0] + sum7_pair[1];
                sum7_group[1] <= sum7_pair[2] + sum7_pair[3];
                sumsq7_group[0] <= sumsq7_pair[0] + sumsq7_pair[1];
                sumsq7_group[1] <= sumsq7_pair[2] + sumsq7_pair[3];
                sum13_group[0] <= sum13_pair[0] + sum13_pair[1];
                sum13_group[1] <= sum13_pair[2] + sum13_pair[3];
                sum13_group[2] <= sum13_pair[4] + sum13_pair[5];
                sum13_group[3] <= sum13_pair[6];
                sumsq13_group[0] <= sumsq13_pair[0] + sumsq13_pair[1];
                sumsq13_group[1] <= sumsq13_pair[2] + sumsq13_pair[3];
                sumsq13_group[2] <= sumsq13_pair[4] + sumsq13_pair[5];
                sumsq13_group[3] <= sumsq13_pair[6];

                stats_valid  <= reduce_valid2;
                sum7_pipe    <= sum7_group[0] + sum7_group[1];
                sumsq7_pipe  <= sumsq7_group[0] + sumsq7_group[1];
                sum13_pipe   <= (sum13_group[0] + sum13_group[1]) +
                                (sum13_group[2] + sum13_group[3]);
                sumsq13_pipe <= (sumsq13_group[0] + sumsq13_group[1]) +
                                (sumsq13_group[2] + sumsq13_group[3]);
                last_pipe <= {last_pipe[24:0], pending_last};
            end

            if (input_fire) begin
                // Start each raster row from a known horizontal history. The
                // line memory supplies the vertical pixels; clearing the old
                // horizontal tail prevents stale/unknown columns from
                // contaminating the incremental statistics.
                if (x_count == 16'd0) begin
                    for (j = 0; j < 12; j = j + 1)
                        hshift[0][j] <= 8'd0;
                    hshift[0][12] <= s_axis_tdata;

                    for (i = 1; i <= 12; i = i + 1) begin
                        for (j = 0; j < 12; j = j + 1)
                            hshift[i][j] <= 8'd0;
                        hshift[i][12] <= incoming_pixel[i];
                    end
                end else begin
                    // Current row shift register.
                    for (j = 0; j < 12; j = j + 1)
                        hshift[0][j] <= hshift[0][j + 1];
                    hshift[0][12] <= s_axis_tdata;

                    // Read the twelve previous rows before overwriting this row slot.
                    for (i = 1; i <= 12; i = i + 1) begin
                        for (j = 0; j < 12; j = j + 1)
                            hshift[i][j] <= hshift[i][j + 1];
                        hshift[i][12] <= incoming_pixel[i];
                    end
                end
                line_mem0[x_count]  <= s_axis_tdata;
                line_mem1[x_count]  <= line_mem0[x_count];
                line_mem2[x_count]  <= line_mem1[x_count];
                line_mem3[x_count]  <= line_mem2[x_count];
                line_mem4[x_count]  <= line_mem3[x_count];
                line_mem5[x_count]  <= line_mem4[x_count];
                line_mem6[x_count]  <= line_mem5[x_count];
                line_mem7[x_count]  <= line_mem6[x_count];
                line_mem8[x_count]  <= line_mem7[x_count];
                line_mem9[x_count]  <= line_mem8[x_count];
                line_mem10[x_count] <= line_mem9[x_count];
                line_mem11[x_count] <= line_mem10[x_count];

                if (x_count == IMAGE_WIDTH-1) begin
                    x_count <= 16'd0;
                    if (y_count == IMAGE_HEIGHT-1) begin
                        y_count <= 16'd0;
                    end else begin
                        y_count <= y_count + 16'd1;
                    end
                end else begin
                    x_count <= x_count + 16'd1;
                end
            end
        end
    end

endmodule
