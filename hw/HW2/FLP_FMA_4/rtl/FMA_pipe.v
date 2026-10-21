`timescale 1ns/1ps

// Four-stage pipelined single-precision floating-point fused multiply-add: d = a*b + c.
// Ports carry the raw IEEE 754 binary32 bit patterns.
module FMA_pipe (
    input clk,
    input rst,
    input  [31:0] a,
    input  [31:0] b,
    input  [31:0] c,
    output reg [31:0] d,
    output reg overflow
);


    // pipeline registers
    reg signed [75:0] mp_s1, mc_s1;
    reg signed [9:0] ep_s1, ep_s2, e_s3;
    reg [75:0] raw_s2, round_s3;
    reg sign_s2, sign_s3, zero_s3;
    reg bypass_valid;
    reg [31:0] bypass_d;
    reg bypass_s1, bypass_s2, bypass_s3;
    reg [31:0] bypass_d_s1, bypass_d_s2, bypass_d_s3;

    reg [31:0] d_next;
    reg overflow_next;

    wire sa, sb, sc;
    wire [9:0] ea, eb, ec;
    wire [23:0] ma, mb, mc;

    wire sp;
    wire a_zero, a_inf, a_nan;
    wire b_zero, b_inf, b_nan;
    wire c_zero, c_inf, c_nan;
    wire p_invalid, p_inf, p_zero;

    // The supplied golden vectors use all ones as the NaN encoding.
    localparam [31:0] NAN_RESULT = 32'hffffffff;



    wire signed [9:0] ep, e_diff, e_norm, ed;

    wire [47:0] mp; // Q2.46
    wire signed [48:0] mp_signed; // Q3.46
    wire signed [75:0] mp_signed_extended; // Q28.48

    wire signed [24:0] mc_signed;
    wire signed [75:0] mc_extended;
    reg discarded_nonzero;
    reg signed [75:0] mc_aligned;

    wire signed [76:0] md_signed; // Q29.48
    wire [75:0] md_raw; // Q28.48
    wire [75:0] md_norm; // Q1.75
    reg [75:0] round_input;
    integer sub_shift;
    wire round_up;
    wire [24:0] md_round; // Q2.23
    wire [22:0] md;

    reg found_one;
    integer bit_index;
    reg [6:0] shift_left;



    // unpack
    assign sa = a[31];
    assign sb = b[31];
    assign sc = c[31];

    assign ea = (a[30:23] == 8'd0) ? 10'd1 : {2'b0, a[30:23]};
    assign eb = (b[30:23] == 8'd0) ? 10'd1 : {2'b0, b[30:23]};
    assign ec = (c[30:23] == 8'd0) ? 10'd1 : {2'b0, c[30:23]};

    assign ma = {(a[30:23] != 8'd0), a[22:0]};
    assign mb = {(b[30:23] != 8'd0), b[22:0]};
    assign mc = {(c[30:23] != 8'd0), c[22:0]};

    // Classify raw encodings before using effective exponents.
    assign a_zero = (a[30:23] == 8'd0) && (a[22:0] == 23'd0);
    assign a_inf = (a[30:23] == 8'hff) && (a[22:0] == 23'd0);
    assign a_nan = (a[30:23] == 8'hff) && (a[22:0] != 23'd0);
    assign b_zero = (b[30:23] == 8'd0) && (b[22:0] == 23'd0);
    assign b_inf = (b[30:23] == 8'hff) && (b[22:0] == 23'd0);
    assign b_nan = (b[30:23] == 8'hff) && (b[22:0] != 23'd0);
    assign c_zero = (c[30:23] == 8'd0) && (c[22:0] == 23'd0);
    assign c_inf = (c[30:23] == 8'hff) && (c[22:0] == 23'd0);
    assign c_nan = (c[30:23] == 8'hff) && (c[22:0] != 23'd0);

    assign p_invalid = (a_zero && b_inf) || (a_inf && b_zero);
    assign p_inf = (a_inf || b_inf) && !p_invalid;
    assign p_zero = a_zero || b_zero;

    // sp
    assign sp = sa ^ sb;

    // exponent path
    assign ep = $signed(ea) + $signed(eb) - 10'sd127;
    assign e_diff = ep - $signed(ec);
    assign e_norm = ep_s2 + 10'sd27 - $signed({3'b000, shift_left}); // md_raw (Q28.48) -> md_norm (Q1.75)
    assign ed = e_s3 + (md_round[24] ? 10'sd1 : 10'sd0);

    // mp path
    assign mp = ma * mb;
    assign mp_signed = sp ? -mp : mp;
    assign mp_signed_extended = {{25{mp_signed[48]}}, mp_signed, 2'b0};

    // mc path
    assign mc_signed = sc ? -mc : mc;
    assign mc_extended = {{26{mc_signed[24]}}, mc_signed, 25'b0};
    always @* begin
        discarded_nonzero = 1'b0;

        if (c_zero) begin
            // Zero must not become a fabricated sticky contribution.
            mc_aligned = 76'sd0;
        end else if (e_diff > 47) begin
            // Preserve the direction of the tiny contribution.
            mc_aligned = sc ? -76'sd1 : 76'sd1;

        end else if (e_diff > 0) begin
            // Check the low bits that the right shift will discard.
            // The mask "((76'd1 << e_diff) - 76'd1)" sets the lowest e_diff bits
            discarded_nonzero = |(mc_extended & ((76'd1 << e_diff) - 76'd1));

            mc_aligned = mc_extended >>> e_diff;

            // Jam discarded information into the lowest retained bit.
            if (discarded_nonzero) mc_aligned[0] = 1'b1;
        end else if (e_diff >= -25) begin
            mc_aligned = mc_extended <<< (-e_diff);

        end else begin
            // The dominant-c case is handled by the output bypass.
            mc_aligned = mc_extended;
        end
    end

    // md path
    assign md_signed = mp_s1 + mc_s1; 
    assign md_raw = raw_s2; // absolute value
    // LOD
    always @* begin
        found_one = 1'b0;
        shift_left = 0;

        for (bit_index = 75; bit_index >= 0; bit_index = bit_index-1) begin
            if (!found_one && md_raw[bit_index]) begin
                found_one = 1'b1;
                shift_left = 75 - bit_index;
            end
        end
    end
    assign md_norm = md_raw << shift_left;
    // Subnormals use the scale of biased exponent 1, without a hidden one.
    // Shift before rounding so the result is rounded only once.
    always @* begin
        round_input = md_norm;
        sub_shift = 0;

        if (e_norm <= 10'sd0) begin
            sub_shift = 1 - e_norm;

            if (sub_shift >= 76) begin
                round_input = 76'b0;
                round_input[0] = |md_norm;
            end else begin
                round_input = md_norm >> sub_shift;
                // Jam nonzero discarded bits into the retained sticky bit.
                round_input[0] = round_input[0] | (|(md_norm & ((76'd1 << sub_shift) - 76'd1)));
            end
        end
    end

    // Round to nearest, ties to even at the normal or subnormal scale.
    // Bit 51 is guard, bit 50 is round, bits 49:0 supply sticky, bit 52 is LSB.
    assign round_up = round_s3[51] && (round_s3[50] || (|round_s3[49:0]) || round_s3[52]);
    assign md_round = {1'b0, round_s3[75:52]} + (round_up ? 25'd1 : 25'd0);
    assign md = md_round[24] ? md_round[23:1] : md_round[22:0];

    // Stage 1 special-value and dominant-c bypass selection.
    always @* begin
        bypass_valid = 1'b1;
        bypass_d = 32'd0;

        if (a_nan || b_nan || c_nan) begin
            bypass_d = NAN_RESULT;
        end else if (p_invalid) begin
            // Zero times infinity is invalid, regardless of c.
            bypass_d = NAN_RESULT;
        end else if (p_inf && c_inf && (sp != sc)) begin
            // Opposite signed infinities cannot be added.
            bypass_d = NAN_RESULT;
        end else if (p_inf) begin
            bypass_d = {sp, 8'hff, 23'd0};
        end else if (c_inf) begin
            bypass_d = c;
        end else if (p_zero && c_zero) begin
            // Under ties-to-even, only two negative zeros sum to negative zero.
            bypass_d = {sp & sc, 31'd0};
        end else if (p_zero) begin
            bypass_d = c;
        end else if (!c_zero && (e_diff < -10'sd25)) begin
            // dominant-c shortcut.
            bypass_d = c;
        end else begin
            bypass_valid = 1'b0;
        end
    end

    // Stage 4: round once and select the output encoding.
    always @* begin
        overflow_next = 1'b0;
        if (bypass_s3)
            d_next = bypass_d_s3;
        else if (zero_s3)
            d_next = 32'd0;
        else if (e_s3 <= 10'sd0)
            d_next = {sign_s3, (md_round[23] ? 8'd1 : 8'd0), md_round[22:0]};
        else if (ed >= 10'sd255) begin
            d_next = {sign_s3, 8'hff, 23'd0};
            overflow_next = 1'b1;
        end else
            d_next = {sign_s3, ed[7:0], md};
    end

    always @(posedge clk) begin
        if (rst) begin
            mp_s1 <= 0; mc_s1 <= 0; ep_s1 <= 0;
            bypass_s1 <= 0; bypass_d_s1 <= 0;

            raw_s2 <= 0; sign_s2 <= 0; ep_s2 <= 0;
            bypass_s2 <= 0; bypass_d_s2 <= 0;

            round_s3 <= 0; e_s3 <= 0; sign_s3 <= 0; zero_s3 <= 1;
            bypass_s3 <= 0; bypass_d_s3 <= 0;

            d <= 0; overflow <= 0;
        end else begin
            // Stage 1: multiplication, alignment, and special classification.
            mp_s1 <= mp_signed_extended;
            mc_s1 <= mc_aligned;
            ep_s1 <= ep;
            bypass_s1 <= bypass_valid;
            bypass_d_s1 <= bypass_d;

            // Stage 2: signed addition and absolute value.
            raw_s2 <= md_signed[76] ? -md_signed : md_signed[75:0];
            sign_s2 <= md_signed[76];
            ep_s2 <= ep_s1;
            bypass_s2 <= bypass_s1;
            bypass_d_s2 <= bypass_d_s1;

            // Stage 3: normalization and subnormal shift with sticky.
            round_s3 <= round_input;
            e_s3 <= e_norm;
            sign_s3 <= sign_s2;
            zero_s3 <= (raw_s2 == 76'd0);
            bypass_s3 <= bypass_s2;
            bypass_d_s3 <= bypass_d_s2;

            // Stage 4: output and overflow for the same transaction.
            d <= d_next;
            overflow <= overflow_next;
        end
    end
endmodule
