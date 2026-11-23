`timescale 1ns/1ps

module DP4_CG (
    input         clk,
    input         rst,
    input         precision,
    input  [31:0] x1, x2, x3, x4,
    input  [31:0] y1, y2, y3, y4,
    output reg [31:0] z
);

    // precision: 0 = FP32, 1 = FP16 in port bits [31:16].
    // Named stage blocks organize inline RTL; no helper modules are instantiated.
    wire [127:0] x, y;
    assign x = {x4, x3, x2, x1};
    assign y = {y4, y3, y2, y1};

    // Bundles carry arithmetic and matching precision/classification metadata.
    wire [274:0] s1_next, s1;
    wire [262:0] s2_next, s2;
    wire [97:0] s3_next, s3;
    wire [99:0] s4_next, s4;

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

    // Stage 1: unpack, operand preparation, product exponents and maximum.
    generate
        if (1) begin : stage1
            wire [32:0] metadata;
            wire [95:0] mx, my;
            reg signed [9:0] emax;
            reg [39:0] ediff;
            wire [3:0] sx, sy, nanx, nany, zerox, zeroy, infx, infy;
            wire [31:0] ex, ey;
            wire [39:0] ep;
            wire [3:0] active;
            reg found;
            integer i;

            // Repeated unpacking, operand preparation and product exponent calculation.
            genvar lane;

            for (lane = 0; lane < 4; lane = lane + 1) begin : lanes
                assign {sx[lane], ex[8 * lane +: 8], mx[24 * lane +: 24],
                        nanx[lane], infx[lane], zerox[lane]} =
                    unpack_operand(precision, x[32 * lane +: 32]);
                assign {sy[lane], ey[8 * lane +: 8], my[24 * lane +: 24],
                        nany[lane], infy[lane], zeroy[lane]} =
                    unpack_operand(precision, y[32 * lane +: 32]);

                // Subtract one format bias now; ep remains output-biased and signed.
                assign ep[10 * lane +: 10] =
                    $signed({2'b00, ex[8 * lane +: 8]}) +
                    $signed({2'b00, ey[8 * lane +: 8]}) -
                    (precision ? 10'sd15 : 10'sd127);
                assign active[lane] = !(nanx[lane] | nany[lane] |
                    infx[lane] | infy[lane] | zerox[lane] | zeroy[lane]);
            end

            assign metadata = {precision, sx, sy, nanx, nany, zerox, zeroy, infx, infy};

            // Ignore zero and exceptional products when choosing the alignment anchor.
            always @* begin
                emax = 10'sd0;
                found = 1'b0;
                ediff = 40'd0;
                for (i = 0; i < 4; i = i + 1) begin
                    if (active[i] && (!found || $signed(ep[10 * i +: 10]) > emax)) begin
                        emax = $signed(ep[10 * i +: 10]);
                        found = 1'b1;
                    end
                end
                for (i = 0; i < 4; i = i + 1) begin
                    if (active[i])
                        ediff[10 * i +: 10] = emax - $signed(ep[10 * i +: 10]);
                end
            end
            assign s1_next = {metadata, emax, ediff, mx, my};
        end
    endgenerate

    // Freeze both operands of the three FP32-only partial multipliers in FP16.
    // Low halves need dedicated retained copies: the low-low path keeps changing.
    reg fp32_enable_latched;
    wire fp32_clk;
    reg [95:0] fp32_mx, fp32_my;

    // A low-phase latch prevents precision/reset changes from creating clock edges.
    // Reset forces the gate open so this bank shares the synchronous reset contract.
    always @* begin
        if (!clk)
            fp32_enable_latched = rst || !precision;
    end
    assign fp32_clk = clk & fp32_enable_latched;

    always @(posedge fp32_clk) begin
        if (rst) begin
            fp32_mx <= 96'd0;
            fp32_my <= 96'd0;
        end else begin
            fp32_mx <= s1_next[191:96];
            fp32_my <= s1_next[95:0];
        end
    end

    // Stage 2: partial multiplication, Q2.50 alignment and signed conversion.
    generate
        if (1) begin : stage2
            wire [32:0] metadata;
            wire [39:0] ediff;
            wire [95:0] mx, my;
            wire [219:0] p_signed;
            assign {metadata, ediff, mx, my} =
                {s1[274:242], s1[231:192], s1[191:96], s1[95:0]};
            // The shared grid is Q2.50 in both modes, with magnitude truncation.
            genvar lane;

            for (lane = 0; lane < 4; lane = lane + 1) begin : lanes
                wire [23:0] mxi, myi, fp32_mxi, fp32_myi;
                wire [47:0] p;
                wire [51:0] scaled, p_aligned;
                wire signed [54:0] magnitude;
                wire sp;
                wire [23:0] p_ll, p_lh, p_hl, p_hh;
                wire [47:0] p_full;

                assign mxi = mx[24 * lane +: 24];
                assign myi = my[24 * lane +: 24];
                assign fp32_mxi = fp32_mx[24 * lane +: 24];
                assign fp32_myi = fp32_my[24 * lane +: 24];
                assign sp = metadata[28 + lane] ^ metadata[24 + lane];

                // Four unsigned 12x12 partial products; FP16 uses low-low only.
                assign p_ll = mxi[11:0] * myi[11:0];
                assign p_lh = fp32_mxi[11:0] * fp32_myi[23:12];
                assign p_hl = fp32_mxi[23:12] * fp32_myi[11:0];
                assign p_hh = fp32_mxi[23:12] * fp32_myi[23:12];
                assign p_full = {p_hh, p_ll} + {12'd0, p_lh, 12'd0} +
                                {12'd0, p_hl, 12'd0};
                assign p = metadata[32] ? {24'd0, p_ll} : p_full;

                assign scaled = metadata[32] ? {p[21:0], 30'd0} : {p, 4'd0};
                assign p_aligned = scaled >> ediff[10 * lane +: 10];
                assign magnitude = $signed({3'b000, p_aligned});
                assign p_signed[55 * lane +: 55] = sp ? -magnitude : magnitude;
            end

            assign s2_next = {metadata, s1[241:232], p_signed};
        end
    endgenerate

    // Stage 3: balanced signed Q5.50 accumulation.
    wire signed [54:0] pair01, pair23, sum;
    assign pair01 = $signed(s2[54:0]) + $signed(s2[109:55]);
    assign pair23 = $signed(s2[164:110]) + $signed(s2[219:165]);
    assign sum = pair01 + pair23;
    assign s3_next = {s2[262:230], s2[229:220], sum};

    // Stage 4: magnitude, normalization exponent and underflow shift.
    generate
        if (1) begin : stage4
            wire signed [54:0] sum;
            wire signed [9:0] emax;
            reg [54:0] m_norm;
            reg signed [9:0] enorm;
            reg sz, is_zero;
            assign sum = $signed(s3[54:0]);
            assign emax = $signed(s3[64:55]);
            reg [54:0] magnitude;
            reg signed [6:0] adj_norm;
            integer leading, i;

            // Right-shift with jamming preserves only final rounding information.
            // This is used after signed accumulation, never instead of exact low terms.
            function [54:0] shift_jam;
                input [54:0] value;
                input integer amount;
                reg sticky;
                integer bit_index;
                begin
                    sticky = 1'b0;
                    shift_jam = value >> amount;
                    for (bit_index = 0; bit_index < 55; bit_index = bit_index + 1) begin
                        if (bit_index < amount)
                            sticky = sticky | value[bit_index];
                    end
                    shift_jam[0] = shift_jam[0] | sticky;
                end
            endfunction

            always @* begin
                sz = sum[54];
                magnitude = sz ? -sum : sum;
                is_zero = (magnitude == 0);
                leading = 0;
                adj_norm = 7'sd0;
                m_norm = 55'd0;
                enorm = 10'sd0;

                // Calculate the exponent first; no combinational feedback from norm.
                for (i = 0; i < 55; i = i + 1) begin
                    if (magnitude[i])
                        leading = i;
                end
                if (!is_zero) begin
                    adj_norm = leading - 50;
                    enorm = emax + $signed({{3{adj_norm[6]}}, adj_norm});
                    m_norm = magnitude << (53 - leading);

                    // Normal hidden one is bit 53. Subnormals share exponent field zero.
                    if (enorm <= 0) begin
                        m_norm = shift_jam(m_norm, 1 - enorm);
                        enorm = 10'sd0;
                    end
                end
            end
            assign s4_next = {s3[97:65], is_zero, sz, enorm, m_norm};
        end
    endgenerate

    // S1-S4: synchronous active-high reset, four capture edges of latency.
    // Stage 5 remains combinational and is sampled half a period after S4.
    reg [274:0] s1_reg;
    reg [262:0] s2_reg;
    reg [97:0] s3_reg;
    reg [99:0] s4_reg;

    always @(posedge clk) begin
        if (rst) begin
            s1_reg <= 275'd0;
            s2_reg <= 263'd0;
            s3_reg <= 98'd0;
            s4_reg <= 100'd0;
        end else begin
            s1_reg <= s1_next;
            s2_reg <= s2_next;
            s3_reg <= s3_next;
            s4_reg <= s4_next;
        end
    end

    assign s1 = s1_reg;
    assign s2 = s2_reg;
    assign s3 = s3_reg;
    assign s4 = s4_reg;

    // Stage 5: GRS rounding, renormalization, packing and priority result mux.
    generate
        if (1) begin : stage5
            wire [32:0] metadata;
            wire [54:0] m_norm;
            wire signed [9:0] enorm;
            wire sz, is_zero;
            assign metadata = s4[99:67];
            assign m_norm = s4[54:0];
            assign enorm = $signed(s4[64:55]);
            assign sz = s4[65];
            assign is_zero = s4[66];
            wire precision;
            wire [3:0] sx, sy, nanx, nany, zerox, zeroy, infx, infy;
            assign {precision, sx, sy, nanx, nany, zerox, zeroy, infx, infy} = metadata;

            reg [24:0] m_round;
            reg signed [9:0] ez;
            reg guard_bit, round_bit, sticky_bit, increment, overflow;
            reg any_nan, positive_inf, negative_inf, all_zero, all_negative;
            reg product_sign, invalid, zero_sign;
            reg [31:0] z_normal, z_nan, z_inf, z_zero;
            integer i;

            always @* begin
                // Retain the hidden bit, extract GRS for the selected output precision.
                m_round = precision ? {14'd0, m_norm[53:43]} : {1'b0, m_norm[53:30]};
                guard_bit = precision ? m_norm[42] : m_norm[29];
                round_bit = precision ? m_norm[41] : m_norm[28];
                sticky_bit = precision ? (|m_norm[40:0]) : (|m_norm[27:0]);
                increment = guard_bit && (round_bit || sticky_bit || m_round[0]);
                m_round = m_round + {24'd0, increment};
                ez = enorm;

                // Renormalize carry, including promotion of a rounded subnormal.
                if (precision ? m_round[11] : m_round[24]) begin
                    m_round = m_round >> 1;
                    ez = ez + 10'sd1;
                end else if ((ez == 0) && (precision ? m_round[10] : m_round[23])) begin
                    ez = 10'sd1;
                end
                overflow = precision ? (ez >= 10'sd31) : (ez >= 10'sd255);

                // Special-value controller uses original operand signs and classifications.
                any_nan = |(nanx | nany);
                invalid = 1'b0;
                positive_inf = 1'b0;
                negative_inf = 1'b0;
                all_zero = 1'b1;
                all_negative = 1'b1;
                product_sign = 1'b0;
                for (i = 0; i < 4; i = i + 1) begin
                    product_sign = sx[i] ^ sy[i];
                    invalid = invalid | (infx[i] && zeroy[i]) | (infy[i] && zerox[i]);
                    if (infx[i] || infy[i]) begin
                        positive_inf = positive_inf | !product_sign;
                        negative_inf = negative_inf | product_sign;
                    end
                    all_zero = all_zero && (zerox[i] || zeroy[i]);
                    all_negative = all_negative && product_sign;
                end
                any_nan = any_nan | invalid | (positive_inf && negative_inf);
                zero_sign = sz | (is_zero && all_zero && all_negative);

                // Pack FP16 into the upper port half; unused lower bits are deterministic.
                z_nan = precision ? 32'h7e000000 : 32'h7fc00000;
                z_zero = {zero_sign, 31'd0};
                if (precision) begin
                    z_normal = {sz, ez[4:0], m_round[9:0], 16'd0};
                    z_inf = {negative_inf, 5'h1f, 10'd0, 16'd0};
                end else begin
                    z_normal = {sz, ez[7:0], m_round[22:0]};
                    z_inf = {negative_inf, 8'hff, 23'd0};
                end

                // Priority result mux: exceptions dominate all finite arithmetic flags.
                z = z_normal;
                if (any_nan)
                    z = z_nan;
                else if (positive_inf || negative_inf)
                    z = z_inf;
                else if (overflow)
                    z = precision ? {sz, 5'h1f, 26'd0} : {sz, 8'hff, 23'd0};
                else if (is_zero)
                    z = z_zero;
            end
        end
    endgenerate

endmodule
