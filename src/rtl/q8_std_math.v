`timescale 1ns / 1ps

// Reference Q8 arithmetic core for one integer window.
// This is intentionally isolated from AXI and line-buffer logic so that the
// fixed-point formula can be checked before building the streaming datapath.
module q8_std_math #(
    parameter integer WINDOW_COUNT = 49
) (
    input  wire [15:0] sum_in,
    input  wire [23:0] sumsq_in,
    output reg  [31:0] mean_q8,
    output reg  [31:0] meansq_q8,
    output reg  [63:0] variance_q2,
    output reg  [15:0] std_q8
);

    // The largest values used by the 7x7/13x13 windows fit in 32 bits after
    // the Q8 scaling. Keeping these intermediates narrow avoids inferring a
    // full-width variable divider.
    reg [31:0] mean_num;
    reg [31:0] meansq_num;
    reg [63:0] meansq_scaled;
    reg [63:0] mean_squared;

    function [15:0] isqrt64;
        input [63:0] value;
        integer i;
        reg [31:0] root;
        reg [65:0] remainder;
        reg [65:0] trial;
        begin
            // Restoring square root. This uses shifts, subtractors and
            // comparators instead of sixteen parallel multipliers.
            root = 32'd0;
            remainder = 66'd0;
            for (i = 15; i >= 0; i = i - 1) begin
                remainder = (remainder << 2) |
                            ((value >> (i * 2)) & 64'd3);
                root = root << 1;
                trial = (root << 1) | 66'd1;
                if (remainder >= trial) begin
                    remainder = remainder - trial;
                    root = root | 32'd1;
                end
            end
            isqrt64 = root[15:0];
        end
    endfunction

    function [31:0] div_window;
        input [31:0] numerator;
        integer i;
        reg [32:0] remainder;
        begin
            // Unsigned restoring division by the compile-time window size.
            // The divisor is 49 or 169 for this design, so no generic
            // run-time divider is required.
            remainder = 33'd0;
            div_window = 32'd0;
            for (i = 31; i >= 0; i = i - 1) begin
                remainder = (remainder << 1) | numerator[i];
                if (remainder >= WINDOW_COUNT) begin
                    remainder = remainder - WINDOW_COUNT;
                    div_window[i] = 1'b1;
                end
            end
        end
    endfunction

    always @* begin
        mean_num   = (sum_in * 32'd256) + (WINDOW_COUNT / 2);
        meansq_num = (sumsq_in * 32'd256) + (WINDOW_COUNT / 2);

        mean_q8   = div_window(mean_num);
        meansq_q8 = div_window(meansq_num);

        meansq_scaled = meansq_q8 * 64'd256;
        mean_squared  = {32'd0, mean_q8} * {32'd0, mean_q8};
        if (meansq_scaled < mean_squared)
            variance_q2 = 64'd0;
        else
            variance_q2 = meansq_scaled - mean_squared;

        std_q8 = isqrt64(variance_q2);
    end

endmodule

// Throughput-oriented implementation for the streaming core. The divide,
// variance and each restoring-square-root iteration are separated by
// registers. A single clock enable freezes the complete pipeline when AXI
// backpressure reaches the output.
module q8_std_math_pipe #(
    parameter integer WINDOW_COUNT = 49
) (
    input  wire        clk,
    input  wire        resetn,
    input  wire        ce,
    input  wire        valid_in,
    input  wire [15:0] sum_in,
    input  wire [23:0] sumsq_in,
    output wire        valid_out,
    output wire [15:0] std_q8
);

    localparam [39:0] RECIPROCAL =
        (WINDOW_COUNT == 49) ? 40'h05397829cb : 40'h0183c977ab;
    localparam [7:0] DIVISOR = WINDOW_COUNT;

    reg        input_valid;
    reg        product_valid;
    reg        quotient_valid;
    reg        threshold_valid;
    reg        div_valid;
    reg [31:0] mean_num_reg;
    reg [31:0] meansq_num_reg;
    reg [31:0] mean_num_delay1;
    reg [31:0] meansq_num_delay1;
    reg [31:0] mean_num_delay2;
    reg [31:0] meansq_num_delay2;
    reg [31:0] mean_num_delay3;
    reg [31:0] meansq_num_delay3;
    reg [71:0] mean_product_reg;
    reg [71:0] meansq_product_reg;
    reg [31:0] mean_quotient_est;
    reg [31:0] meansq_quotient_est;
    reg [31:0] mean_quotient_delay;
    reg [31:0] meansq_quotient_delay;
    reg [31:0] mean_next_multiple;
    reg [31:0] meansq_next_multiple;
    reg [31:0] mean_q8_reg;
    reg [31:0] meansq_q8_reg;

    reg        variance_terms_valid;
    reg        variance_valid;
    reg [31:0] variance_reg;
    reg [31:0] meansq_scaled_reg;
    reg [31:0] mean_squared_reg;

    reg        sqrt_valid [0:15];
    reg [31:0] radicand_pipe [0:15];
    reg [32:0] remainder_pipe [0:15];
    reg [15:0] root_pipe [0:15];

    integer i;

    function [48:0] sqrt_step;
        input [32:0] remainder_in;
        input [15:0] root_in;
        input [1:0]  pair_in;
        reg [32:0] remainder_next;
        reg [15:0] root_next;
        reg [16:0] trial;
        begin
            remainder_next = (remainder_in << 2) | pair_in;
            root_next = root_in << 1;
            trial = ({1'b0, root_next} << 1) | 17'd1;
            if (remainder_next >= trial) begin
                remainder_next = remainder_next - trial;
                root_next = root_next | 16'd1;
            end
            sqrt_step = {remainder_next, root_next};
        end
    endfunction

    assign valid_out = sqrt_valid[15];
    assign std_q8 = root_pipe[15];

    always @(posedge clk) begin
        if (!resetn) begin
            input_valid    <= 1'b0;
            product_valid  <= 1'b0;
            quotient_valid <= 1'b0;
            threshold_valid <= 1'b0;
            div_valid      <= 1'b0;
            variance_terms_valid <= 1'b0;
            variance_valid <= 1'b0;
            mean_num_reg       <= 32'd0;
            meansq_num_reg     <= 32'd0;
            mean_num_delay1    <= 32'd0;
            meansq_num_delay1  <= 32'd0;
            mean_num_delay2    <= 32'd0;
            meansq_num_delay2  <= 32'd0;
            mean_num_delay3    <= 32'd0;
            meansq_num_delay3  <= 32'd0;
            mean_product_reg   <= 72'd0;
            meansq_product_reg <= 72'd0;
            mean_quotient_est  <= 32'd0;
            meansq_quotient_est <= 32'd0;
            mean_quotient_delay <= 32'd0;
            meansq_quotient_delay <= 32'd0;
            mean_next_multiple <= 32'd0;
            meansq_next_multiple <= 32'd0;
            mean_q8_reg    <= 32'd0;
            meansq_q8_reg  <= 32'd0;
            variance_reg   <= 32'd0;
            meansq_scaled_reg <= 32'd0;
            mean_squared_reg  <= 32'd0;
            for (i = 0; i < 16; i = i + 1) begin
                sqrt_valid[i]     <= 1'b0;
                radicand_pipe[i]  <= 32'd0;
                remainder_pipe[i] <= 33'd0;
                root_pipe[i]      <= 16'd0;
            end
        end else if (ce) begin
            input_valid <= valid_in;
            mean_num_reg <= ({16'd0, sum_in} << 8) +
                            (WINDOW_COUNT / 2);
            meansq_num_reg <= ({8'd0, sumsq_in} << 8) +
                              (WINDOW_COUNT / 2);

            product_valid <= input_valid;
            mean_product_reg <= mean_num_reg * RECIPROCAL;
            meansq_product_reg <= meansq_num_reg * RECIPROCAL;
            mean_num_delay1 <= mean_num_reg;
            meansq_num_delay1 <= meansq_num_reg;

            quotient_valid <= product_valid;
            mean_quotient_est <= mean_product_reg >> 40;
            meansq_quotient_est <= meansq_product_reg >> 40;
            mean_num_delay2 <= mean_num_delay1;
            meansq_num_delay2 <= meansq_num_delay1;

            // floor(2^40 / d) gives either the exact quotient or one less.
            // Register the next multiple before comparing so the constant
            // multiply and quotient correction do not share one cycle.
            threshold_valid <= quotient_valid;
            mean_num_delay3 <= mean_num_delay2;
            meansq_num_delay3 <= meansq_num_delay2;
            mean_quotient_delay <= mean_quotient_est;
            meansq_quotient_delay <= meansq_quotient_est;
            mean_next_multiple <= (mean_quotient_est + 32'd1) * DIVISOR;
            meansq_next_multiple <= (meansq_quotient_est + 32'd1) * DIVISOR;

            div_valid <= threshold_valid;
            mean_q8_reg <= mean_quotient_delay +
                           (mean_num_delay3 >= mean_next_multiple);
            meansq_q8_reg <= meansq_quotient_delay +
                             (meansq_num_delay3 >= meansq_next_multiple);

            // Both terms are exactly 32 bits for 8-bit source pixels. Register
            // them before the compare/subtract so the DSP square and the
            // carry chain occupy separate clock cycles.
            variance_terms_valid <= div_valid;
            meansq_scaled_reg <= {meansq_q8_reg[23:0], 8'd0};
            mean_squared_reg <= mean_q8_reg[15:0] * mean_q8_reg[15:0];

            variance_valid <= variance_terms_valid;
            if (meansq_scaled_reg < mean_squared_reg)
                variance_reg <= 32'd0;
            else
                variance_reg <= meansq_scaled_reg - mean_squared_reg;

            sqrt_valid[0] <= variance_valid;
            radicand_pipe[0] <= variance_reg << 2;
            {remainder_pipe[0], root_pipe[0]} <=
                sqrt_step(33'd0, 16'd0, variance_reg[31:30]);

            for (i = 1; i < 16; i = i + 1) begin
                sqrt_valid[i] <= sqrt_valid[i-1];
                radicand_pipe[i] <= radicand_pipe[i-1] << 2;
                {remainder_pipe[i], root_pipe[i]} <=
                    sqrt_step(remainder_pipe[i-1], root_pipe[i-1],
                              radicand_pipe[i-1][31:30]);
            end
        end
    end

endmodule
