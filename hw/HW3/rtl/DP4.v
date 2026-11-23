`timescale 1ns/1ps

module DP4 (
    input         clk,
    input         rst,
    input         precision,
    input  [31:0] x1, x2, x3, x4,
    input  [31:0] y1, y2, y3, y4,
    output reg [31:0] z
);

    // precision: 0 = FP32, 1 = FP16 in port bits [31:16].
    // Entirely combinational: no flip-flops, pipeline registers, or clock latency.
    // clk and rst are retained for the required interface and do not affect z.
    // Arithmetic sections below do not represent clock boundaries.
    wire [127:0] x, y;
    assign x = {x4, x3, x2, x1};
    assign y = {y4, y3, y2, y1};

    // Combinational buses carry arithmetic and precision/classification metadata.
    wire [274:0] unpacked_data;
    wire [262:0] products_data;
    wire [97:0] sum_data;
    wire [99:0] normalized_data;

    // Decode one operand and prepare the hidden bit for the multiplier.
    function [35:0] unpack_operand;
        input precision_mode;
        input [31:0] value;
        reg sign;
        reg [7:0] exponent, raw_exp;
        reg [23:0] mantissa;
        reg [22:0] fraction;
        reg is_nan, is_inf, is_zero, special;
        begin
            sign = value[31];
            raw_exp = precision_mode ? {3'b000, value[30:26]} : value[30:23];
            fraction = precision_mode ? {13'd0, value[25:16]} : value[22:0];
            special = precision_mode ? (raw_exp == 8'd31) : (raw_exp == 8'd255);
            exponent = (raw_exp == 0) ? 8'd1 : raw_exp;
            is_nan = special && (fraction != 0);
            is_inf = special && (fraction == 0);
            is_zero = (raw_exp == 0) && (fraction == 0);

            // Zero special-value mantissas; the final controller selects their result.
            mantissa = 24'd0;
            if (!special) begin
                if (precision_mode)
                    mantissa = {13'd0, (raw_exp != 0), fraction[9:0]};
                else
                    mantissa = {(raw_exp != 0), fraction};
            end

            unpack_operand = {sign, exponent, mantissa, is_nan, is_inf, is_zero};
        end
    endfunction

    // Operand unpacking: unpack, operand preparation, product exponents and maximum.
    wire [32:0] unpack_metadata;
    wire [95:0] unpack_mx, unpack_my;
    reg signed [9:0] unpack_emax;
    reg [39:0] unpack_ediff;
    wire [3:0] unpack_sx, unpack_sy, unpack_nanx, unpack_nany, unpack_zerox, unpack_zeroy, unpack_infx, unpack_infy;
    wire [31:0] unpack_ex, unpack_ey;
    wire [39:0] unpack_ep;
    wire [3:0] unpack_active;
    reg unpack_found;
    integer unpack_i;

    // Repeated unpacking, operand preparation and product exponent calculation.
    genvar unpack_lane;

    generate
        for (unpack_lane = 0; unpack_lane < 4; unpack_lane = unpack_lane + 1) begin : unpack_lanes
            assign {unpack_sx[unpack_lane], unpack_ex[8 * unpack_lane +: 8], unpack_mx[24 * unpack_lane +: 24],
                    unpack_nanx[unpack_lane], unpack_infx[unpack_lane], unpack_zerox[unpack_lane]} =
                unpack_operand(precision, x[32 * unpack_lane +: 32]);
            assign {unpack_sy[unpack_lane], unpack_ey[8 * unpack_lane +: 8], unpack_my[24 * unpack_lane +: 24],
                    unpack_nany[unpack_lane], unpack_infy[unpack_lane], unpack_zeroy[unpack_lane]} =
                unpack_operand(precision, y[32 * unpack_lane +: 32]);

            // Subtract one format bias now; ep remains output-biased and signed.
            assign unpack_ep[10 * unpack_lane +: 10] =
                $signed({2'b00, unpack_ex[8 * unpack_lane +: 8]}) +
                $signed({2'b00, unpack_ey[8 * unpack_lane +: 8]}) -
                (precision ? 10'sd15 : 10'sd127);
            assign unpack_active[unpack_lane] = !(unpack_nanx[unpack_lane] | unpack_nany[unpack_lane] |
                unpack_infx[unpack_lane] | unpack_infy[unpack_lane] | unpack_zerox[unpack_lane] | unpack_zeroy[unpack_lane]);
        end
    endgenerate

    assign unpack_metadata = {precision, unpack_sx, unpack_sy, unpack_nanx, unpack_nany, unpack_zerox, unpack_zeroy, unpack_infx, unpack_infy};

    // Ignore zero and exceptional products when choosing the alignment anchor.
    always @* begin
        unpack_emax = 10'sd0;
        unpack_found = 1'b0;
        unpack_ediff = 40'd0;
        for (unpack_i = 0; unpack_i < 4; unpack_i = unpack_i + 1) begin
            if (unpack_active[unpack_i] && (!unpack_found || $signed(unpack_ep[10 * unpack_i +: 10]) > unpack_emax)) begin
                unpack_emax = $signed(unpack_ep[10 * unpack_i +: 10]);
                unpack_found = 1'b1;
            end
        end
        for (unpack_i = 0; unpack_i < 4; unpack_i = unpack_i + 1) begin
            if (unpack_active[unpack_i])
                unpack_ediff[10 * unpack_i +: 10] = unpack_emax - $signed(unpack_ep[10 * unpack_i +: 10]);
        end
    end
    assign unpacked_data = {unpack_metadata, unpack_emax, unpack_ediff, unpack_mx, unpack_my};

    // Product calculation: partial multiplication, Q2.50 alignment and signed conversion.
    wire [32:0] multiply_metadata;
    wire [39:0] multiply_ediff;
    wire [95:0] multiply_mx, multiply_my;
    wire [219:0] multiply_p_signed;
    assign {multiply_metadata, multiply_ediff, multiply_mx, multiply_my} =
        {unpacked_data[274:242], unpacked_data[231:192], unpacked_data[191:96], unpacked_data[95:0]};
    // The shared grid is Q2.50 in both modes, with magnitude truncation.
    genvar multiply_lane;

    generate
        for (multiply_lane = 0; multiply_lane < 4; multiply_lane = multiply_lane + 1) begin : multiply_lanes
            wire [23:0] mxi, myi;
            wire [47:0] p;
            wire [51:0] scaled, p_aligned;
            wire signed [54:0] magnitude;
            wire sp;
            wire [23:0] p_ll, p_lh, p_hl, p_hh;
            wire [47:0] p_full;

            assign mxi = multiply_mx[24 * multiply_lane +: 24];
            assign myi = multiply_my[24 * multiply_lane +: 24];
            assign sp = multiply_metadata[28 + multiply_lane] ^ multiply_metadata[24 + multiply_lane];

            // Four unsigned 12x12 partial products; FP16 uses low-low only.
            assign p_ll = mxi[11:0] * myi[11:0];
            assign p_lh = mxi[11:0] * myi[23:12];
            assign p_hl = mxi[23:12] * myi[11:0];
            assign p_hh = mxi[23:12] * myi[23:12];
            assign p_full = {p_hh, p_ll} + {12'd0, p_lh, 12'd0} +
                            {12'd0, p_hl, 12'd0};
            assign p = multiply_metadata[32] ? {24'd0, p_ll} : p_full;

            assign scaled = multiply_metadata[32] ? {p[21:0], 30'd0} : {p, 4'd0};
            assign p_aligned = scaled >> multiply_ediff[10 * multiply_lane +: 10];
            assign magnitude = $signed({3'b000, p_aligned});
            assign multiply_p_signed[55 * multiply_lane +: 55] = sp ? -magnitude : magnitude;
        end
    endgenerate

    assign products_data = {multiply_metadata, unpacked_data[241:232], multiply_p_signed};

    // Accumulation: balanced signed Q5.50 accumulation.
    wire signed [54:0] pair01, pair23, sum;
    assign pair01 = $signed(products_data[54:0]) + $signed(products_data[109:55]);
    assign pair23 = $signed(products_data[164:110]) + $signed(products_data[219:165]);
    assign sum = pair01 + pair23;
    assign sum_data = {products_data[262:230], products_data[229:220], sum};

    // Normalization: magnitude, normalization exponent and underflow shift.
    wire signed [54:0] normalize_sum;
    wire signed [9:0] normalize_emax;
    reg [54:0] normalize_m_norm;
    reg signed [9:0] normalize_enorm;
    reg normalize_sz, normalize_is_zero;
    assign normalize_sum = $signed(sum_data[54:0]);
    assign normalize_emax = $signed(sum_data[64:55]);
    reg [54:0] normalize_magnitude;
    reg signed [6:0] normalize_adj_norm;
    integer normalize_leading, normalize_i;

    // Right-shift with jamming preserves only final rounding information.
    // This is used after signed accumulation, never instead of exact low terms.
    function [54:0] normalize_shift_jam;
        input [54:0] value;
        input integer amount;
        reg sticky;
        integer bit_index;
        begin
            sticky = 1'b0;
            normalize_shift_jam = value >> amount;
            for (bit_index = 0; bit_index < 55; bit_index = bit_index + 1) begin
                if (bit_index < amount)
                    sticky = sticky | value[bit_index];
            end
            normalize_shift_jam[0] = normalize_shift_jam[0] | sticky;
        end
    endfunction

    always @* begin
        normalize_sz = normalize_sum[54];
        normalize_magnitude = normalize_sz ? -normalize_sum : normalize_sum;
        normalize_is_zero = (normalize_magnitude == 0);
        normalize_leading = 0;
        normalize_adj_norm = 7'sd0;
        normalize_m_norm = 55'd0;
        normalize_enorm = 10'sd0;

        // Calculate the exponent first; no combinational feedback from norm.
        for (normalize_i = 0; normalize_i < 55; normalize_i = normalize_i + 1) begin
            if (normalize_magnitude[normalize_i])
                normalize_leading = normalize_i;
        end
        if (!normalize_is_zero) begin
            normalize_adj_norm = normalize_leading - 50;
            normalize_enorm = normalize_emax + $signed({{3{normalize_adj_norm[6]}}, normalize_adj_norm});
            normalize_m_norm = normalize_magnitude << (53 - normalize_leading);

            // Normal hidden one is bit 53. Subnormals share exponent field zero.
            if (normalize_enorm <= 0) begin
                normalize_m_norm = normalize_shift_jam(normalize_m_norm, 1 - normalize_enorm);
                normalize_enorm = 10'sd0;
            end
        end
    end
    assign normalized_data = {sum_data[97:65], normalize_is_zero, normalize_sz, normalize_enorm, normalize_m_norm};

    // Output conversion: GRS rounding, renormalization, packing and priority result mux.
    wire [32:0] pack_metadata;
    wire [54:0] pack_m_norm;
    wire signed [9:0] pack_enorm;
    wire pack_sz, pack_is_zero;
    assign pack_metadata = normalized_data[99:67];
    assign pack_m_norm = normalized_data[54:0];
    assign pack_enorm = $signed(normalized_data[64:55]);
    assign pack_sz = normalized_data[65];
    assign pack_is_zero = normalized_data[66];
    wire pack_precision;
    wire [3:0] pack_sx, pack_sy, pack_nanx, pack_nany, pack_zerox, pack_zeroy, pack_infx, pack_infy;
    assign {pack_precision, pack_sx, pack_sy, pack_nanx, pack_nany, pack_zerox, pack_zeroy, pack_infx, pack_infy} = pack_metadata;

    reg [24:0] pack_m_round;
    reg signed [9:0] pack_ez;
    reg pack_guard_bit, pack_round_bit, pack_sticky_bit, pack_increment, pack_overflow;
    reg pack_any_nan, pack_positive_inf, pack_negative_inf, pack_all_zero, pack_all_negative;
    reg pack_product_sign, pack_invalid, pack_zero_sign;
    reg [31:0] pack_z_normal, pack_z_nan, pack_z_inf, pack_z_zero;
    integer pack_i;

    always @* begin
        // Retain the hidden bit, extract GRS for the selected output precision.
        pack_m_round = pack_precision ? {14'd0, pack_m_norm[53:43]} : {1'b0, pack_m_norm[53:30]};
        pack_guard_bit = pack_precision ? pack_m_norm[42] : pack_m_norm[29];
        pack_round_bit = pack_precision ? pack_m_norm[41] : pack_m_norm[28];
        pack_sticky_bit = pack_precision ? (|pack_m_norm[40:0]) : (|pack_m_norm[27:0]);
        pack_increment = pack_guard_bit && (pack_round_bit || pack_sticky_bit || pack_m_round[0]);
        pack_m_round = pack_m_round + {24'd0, pack_increment};
        pack_ez = pack_enorm;

        // Renormalize carry, including promotion of a rounded subnormal.
        if (pack_precision ? pack_m_round[11] : pack_m_round[24]) begin
            pack_m_round = pack_m_round >> 1;
            pack_ez = pack_ez + 10'sd1;
        end else if ((pack_ez == 0) && (pack_precision ? pack_m_round[10] : pack_m_round[23])) begin
            pack_ez = 10'sd1;
        end
        pack_overflow = pack_precision ? (pack_ez >= 10'sd31) : (pack_ez >= 10'sd255);

        // Special-value controller uses original operand signs and classifications.
        pack_any_nan = |(pack_nanx | pack_nany);
        pack_invalid = 1'b0;
        pack_positive_inf = 1'b0;
        pack_negative_inf = 1'b0;
        pack_all_zero = 1'b1;
        pack_all_negative = 1'b1;
        pack_product_sign = 1'b0;
        for (pack_i = 0; pack_i < 4; pack_i = pack_i + 1) begin
            pack_product_sign = pack_sx[pack_i] ^ pack_sy[pack_i];
            pack_invalid = pack_invalid | (pack_infx[pack_i] && pack_zeroy[pack_i]) | (pack_infy[pack_i] && pack_zerox[pack_i]);
            if (pack_infx[pack_i] || pack_infy[pack_i]) begin
                pack_positive_inf = pack_positive_inf | !pack_product_sign;
                pack_negative_inf = pack_negative_inf | pack_product_sign;
            end
            pack_all_zero = pack_all_zero && (pack_zerox[pack_i] || pack_zeroy[pack_i]);
            pack_all_negative = pack_all_negative && pack_product_sign;
        end
        pack_any_nan = pack_any_nan | pack_invalid | (pack_positive_inf && pack_negative_inf);
        pack_zero_sign = pack_sz | (pack_is_zero && pack_all_zero && pack_all_negative);

        // Pack FP16 into the upper port half; unused lower bits are deterministic.
        pack_z_nan = pack_precision ? 32'h7e000000 : 32'h7fc00000;
        pack_z_zero = {pack_zero_sign, 31'd0};
        if (pack_precision) begin
            pack_z_normal = {pack_sz, pack_ez[4:0], pack_m_round[9:0], 16'd0};
            pack_z_inf = {pack_negative_inf, 5'h1f, 10'd0, 16'd0};
        end else begin
            pack_z_normal = {pack_sz, pack_ez[7:0], pack_m_round[22:0]};
            pack_z_inf = {pack_negative_inf, 8'hff, 23'd0};
        end

        // Priority result mux: exceptions dominate all finite arithmetic flags.
        z = pack_z_normal;
        if (pack_any_nan)
            z = pack_z_nan;
        else if (pack_positive_inf || pack_negative_inf)
            z = pack_z_inf;
        else if (pack_overflow)
            z = pack_precision ? {pack_sz, 5'h1f, 26'd0} : {pack_sz, 8'hff, 23'd0};
        else if (pack_is_zero)
            z = pack_z_zero;
    end

endmodule
