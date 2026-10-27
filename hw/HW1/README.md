# Hw 1. Single-precision Floating-point Adder and Multiplier

## 1. Introduction

For ALU Hw1, we implemented signed fixed-point (FXP) and single-precision floating-point (FLP) adders and multipliers. The FLP designs include both combinational and pipelined versions, with a three-stage multiplier and four-stage and seven-stage adders. All FLP designs use round-to-nearest, with halfway cases rounded up. IEEE-754 special values handling (zeroes, infinities, NaNs, and subnormal operands) are outside the scope of this homework.

The Area, Delay, and Between synthesis configurations were compared using PPA. The RTL was verified through pre-sim, and the synthesized netlists were verified through post-sim. APR is not required for Hw1.

## 2. Reproduction Flow

Pre-sim, synthesis, post-sim, and result collection are performed through scripts. Pre-sim is run once per design, and each selected netlist is run through post-sim with its matching SDF file. Full tool output is retained in individual logs and `scripts/run.log`; selected measurements are collected into summary and pipeline CSV files.

```bash
$ bash scripts/run.sh all all --jobs 32
```

## 3. Methodology

- **Pipeline delay report:** `report_timing -from ... -to ...` was used instead of the TA's `-through` method. The first stage is measured from the data input ports to the first register bank's D pins; intermediate stages, from the previous bank's Q pins to the next bank's D pins; and the final stage, from the last bank's Q pins to the output ports. Mantissa, exponent, and sign paths are included within these boundaries. The `-through` option selects a complete path passing through a specified circuit object rather than isolating the delay inside that object. With explicit start and end boundaries, each stage can be reported without depending on stage module names or preserved hierarchy.

- **Reported delay:** Delay is reported as the time taken for data to propagate along the path selected by Design Compiler, from its input port or launching register to its output port or receiving register. This value is read from the `data arrival time` line in the timing report and converted to ns. Ideal clocks and zero external input/output delays are used, so the value includes logic and interconnect delays, plus clock-to-Q delay when the path starts at a register. It excludes the receiving register's setup time—the interval for which data must already be stable before the capturing clock edge. Therefore, reported delay describes data propagation, while a safe clock period must also allow for setup time. Timing slack is checked separately to determine whether that clock-period requirement is met.

- **Automated synthesis frequency tuning:** Slack guides clock-period tuning and indicates whether timing is met. Area and Delay searches select separate netlists; Between starts at the midpoint of their selected periods. Each iteration proposes `p - slack`, shortening the period for positive slack and lengthening it for negative slack. An out-of-bounds or repeated proposal falls back to the midpoint of the known failing/passing interval. A passing result is accepted when slack is within tolerance, Between reaches its lower bound, or the interval is no wider than the period resolution. This proposal estimates the next synthesis constraint; circuit delay is still read from `data arrival time`. Because synthesis jointly optimizes timing, area, and power, each attempt may produce different logic. Testing nearby periods can increase confidence at the cost of runtime.

## 4. Design

### 4.1 FXP [adder](RTL/FXP_adder/FXP_adder.v) & [multiplier](RTL/FXP_mul/FXP_mul.v)

The FXP adder was implemented using two signed 32-bit operands and a signed 33-bit output. Both operands are sign-extended before addition so that the carry and sign information of the full sum are preserved. The FXP multiplier uses two signed 32-bit operands and produces a signed 64-bit product. Both designs are fully combinational and provide the fixed-point baseline for comparison with the floating-point circuits.

### 4.2 FLP multiplier

The basic floating-point multiplication algorithm multiplies the significands and adds the exponents:

$$
(\pm s_1 b^{e_1}) \times (\pm s_2 b^{e_2})
= (\pm s_1 s_2)b^{e_1+e_2}
$$

Here, $b$ is the radix ($b=2$ for binary floating point), $s_1$ and $s_2$ are the input significand magnitudes, and $e_1$ and $e_2$ are their unbiased exponents. The result sign is positive for equal input signs and negative for opposite signs; the product significand and exponent are then adjusted during normalization.

#### 4.2.1 [Combinational](RTL/FLP_mul/FLP_mul.v)

<p align="center">
    <img src="images/FLP_mul_guide.png" alt="Floating-point multiplier guide" width="49.21%" />
    <img src="images/FLP_mul.jpg" alt="Combinational floating-point multiplier" width="46.79%" />
</p>

The operands are unpacked into their signs, biased exponents, and significands. For normalized operands, the implicit leading one is restored to form two 24-bit Q1.23 significands. Their unsigned multiplication produces a full 48-bit Q2.46 product. The output sign is obtained by XORing the input signs, and the exponent is initially calculated as the sum of the biased input exponents minus 127. The product is then normalized, rounded, and renormalized before packing.

1. **Normalization:** Each input significand lies in the range [1, 2), so their product lies in [1, 4). A product of two or greater is shifted right by one, and the exponent is incremented to preserve its value. Otherwise, no shift is needed. After normalization, the significand lies in [1, 2) and is retained as Q1.24 for rounding. Its leading one is kept implicit, so only 24 bits are stored: the output fraction and one guard bit.

2. **Rounding:** Round-to-nearest requires information beyond the final Q1.23 significand; simply discarding all lower bits would truncate the result. The guard bit indicates whether the discarded part is at least half of one step between representable significands. If it is zero, the retained value is unchanged; if it is one, the retained significand is incremented. Under the homework's halfway-up rule, exact halfway cases are also rounded toward the larger magnitude. The leading one is restored for this addition, and a further bit is reserved for carry, giving a 25-bit Q2.23 result. The guard bit is used to decide the increment and is not retained in the output.

3. **Renormalization:** Rounding can change a significand just below two into exactly two. In that case, the rounded significand is shifted right once and the exponent is incremented again, restoring the normalized range [1, 2). This second normalization is needed because the first normalization occurs before rounding and cannot account for a carry created by the rounding addition. Finally, the implicit leading one is omitted, and the sign, eight-bit exponent, and 23 fraction bits are packed into the 32-bit output.

The full 48-bit product is required to preserve the multiplication result before normalization; the additional guard and carry bits are retained specifically to support rounding correctly. Following the homework simplification, the lower eight exponent bits are packed without overflow checking.

#### 4.2.2 [Pipeline 3](RTL/FLP_mul_3/FLP_mul_3.v)

<img src="images/FLP_mul_3.jpg" alt="Three-stage floating-point multiplier" width="60%" style="display: block; margin: 0 auto;">

Multiplication is divided into three stages:

1. Unpack the operands and compute the output sign.
2. Multiply the significands and calculate the initial output exponent.
3. Normalize, round, renormalize if necessary, and pack the output.

There are two internal register banks, with a combinational final stage, matching the TA checker schedule. The first bank stores `s1_ma`, `s1_mb`, `s1_ea`, `s1_eb`, and `s1_sd`. The second stores `s2_md_raw`, `s2_esum`, and `s2_sd`.

### 4.3 FLP adder

The basic floating-point add/subtract algorithm aligns the significands to a common exponent before addition or subtraction. Assuming $e_1 \ge e_2$:

$$
\begin{aligned}
(\pm s_1 b^{e_1}) + (\pm s_2 b^{e_2})
&= (\pm s_1 b^{e_1}) + \left(\pm \frac{s_2}{b^{e_1-e_2}}\right)b^{e_1} \\
&= \left(\pm s_1 \pm \frac{s_2}{b^{e_1-e_2}}\right)b^{e_1}
= \pm s b^e
\end{aligned}
$$

Here, $b=2$ for binary floating point, $s_1$ and $s_2$ are the input significands, and $s$ and $e$ are the normalized result significand and exponent.

#### 4.3.1 [Combinational](RTL/FLP_adder/FLP_adder.v)

<p align="center">
    <img src="images/FLP_adder_guide.png" alt="Floating-point adder guide" width="47.56%" />
    <img src="images/FLP_adder.jpg" alt="Combinational floating-point adder" width="48.44%" />
</p>

The input exponents are compared, and the sign, exponent, and fraction fields are swapped together so that the first operand has the larger exponent. The hidden leading one is restored, and two low guard bits are appended to form 28-bit Q3.25 operands. The arithmetic is then performed through the following steps:

1. **Sign-magnitude to two's complement:** Conversion is performed immediately after unpacking and before alignment. A positive significand is kept unchanged; a negative significand is negated in two's complement. Both addition and effective subtraction can then be performed by the same signed FXP adder, without a separate magnitude-subtraction circuit. Subtraction of the second floating-point operand is represented by reversing its sign before this conversion.

2. **Pre-shift for alignment:** The smaller-exponent significand is arithmetically right-shifted by the absolute exponent difference so that both operands have the same exponent before addition. After operand swapping, this difference is nonnegative. With Q3.25 precision, shifts up to 25 positions can still place the smaller operand's leading bit within the retained fractional field. For `|e_diff| > 25`, its magnitude falls below the least significant internal bit, so a large-shift bypass could be used under the homework's finite-precision approximation. The boundary at 25 should not be discarded automatically because a retained bit remains. The current RTL uses an arithmetic shift for every difference rather than an explicit bypass; large shifts of negative operands retain sign-extension bits. This cutoff is a precision-based simplification, not a general rule for fully correctly rounded IEEE 754 arithmetic.

3. **Two's complement back to sign-magnitude:** Conversion is performed immediately after signed addition and before normalization. The result sign is taken from the signed sum, and a negative sum is negated to obtain its unsigned magnitude. Normalization and rounding are then performed on this magnitude, while the sign is carried separately. Exact cancellation is detected and packed as positive zero.

4. **Near-path cancellation and full normalization:** When opposite-sign operands have equal or adjacent exponents, many leading bits can cancel. This is the near, or close, case, where the result may require a large left shift. For other cases—the far case—normalization requires at most a one-position shift. These cases are commonly separated in dual-path adders, but a single shared normalization circuit is used here. The near/far distinction explains why the full normalization shifter is needed; separate near and far datapaths were not implemented.

5. **Leading-zero detection (LZD):** The leading zeros of the unsigned Q3.25 result are counted to locate its first one. The normalization shift is calculated as `zero_count - 2`: a magnitude of at least two is shifted right once, an already normalized magnitude is unchanged, and a smaller nonzero magnitude is shifted left by the required amount. The exponent is decreased for a left shift and increased for a right shift so that the represented value is preserved. This full LZD-and-shift operation handles cancellation as well as ordinary addition.

6. **Rounding and packing:** The normalized magnitude is rounded to Q1.23 using the guard bit. If rounding creates a carry into the next integer position, the result is shifted right once and the exponent is incremented. The exponent bias is restored, and the sign, lower eight exponent bits, and 23 fraction bits are packed. Exponent wrapping and normalized operands are used under the homework scope.

#### 4.3.2 [Pipeline 4](RTL/FLP_adder_4/FLP_adder_4.v)

<img src="images/FLP_adder_4.jpg" alt="Four-stage floating-point adder" width="60%" style="display: block; margin: 0 auto;">

The adder operations are grouped into four stages:

1. Unpack, convert to two's complement, and align the operands.
2. Perform signed FXP addition and convert the result to sign-magnitude.
3. Normalize the magnitude and adjust the exponent.
4. Round, renormalize if necessary, and pack the output.

#### 4.3.3 [Pipeline 7](RTL/FLP_adder_7/FLP_adder_7.v)

<img src="images/FLP_adder_7.jpg" alt="Seven-stage floating-point adder" width="60%" style="display: block; margin: 0 auto;">

The adder is separated into seven stages:

1. Unpack and convert the operands to two's complement.
2. Align the smaller-exponent operand.
3. Perform signed FXP addition.
4. Convert the sum to sign-magnitude.
5. Normalize and adjust the exponent.
6. Round and renormalize if necessary.
7. Restore the exponent bias and pack the output.

## 5. Verification

### 5.1 Method

The [pre-sim testbench](pre_sim/testbench.v) and [post-sim testbench](post_sim/testbench.v) use the same stimulus and checks. Each run reads 100 pairs of hexadecimal operands, applies reset, and checks the selected design. Combinational outputs are sampled after one configured cycle; pipeline outputs are matched to earlier inputs using the checker’s pipeline schedule. Post-sim additionally annotates the synthesized netlist with its matching SDF file. A run passes only when all 100 results have been checked with no mismatches; completion status is written to [tb_result.json](pre_sim/FLP_adder/tb_result.json).

- **FXP checks:** Signed addition and multiplication are compared bit-for-bit against 33-bit and 64-bit reference results, respectively. These widths preserve the full result, so no rounding tolerance or absolute-error statistic is needed for the encoded operands.
- **FLP checks:** Encoded single-precision operands are converted to double-precision Verilog `real` values for reference addition or multiplication. The combinational checker rounds the reference to single precision using round-to-nearest, ties-to-even, and requires exact bit equality. The pipeline checker truncates the reference significand and accepts that encoding or either adjacent encoding. This tolerance accommodates small rounding differences; the RTL’s halfway-up rule can differ from the combinational reference on exact ties.

### 5.2 Error Analysis

<p align="center">
    <img src="images/midterm_review_slide-26.png" alt="Slide 26: Sources of computational errors" width="48%" />
    <img src="images/midterm_review_slide-27.png" alt="Slide 27: Fixed-point addition and multiplication error" width="48%" />
    <img src="images/midterm_review_slide-28.png" alt="Slide 28: Floating-point multiplication and division error analysis" width="48%" />
    <img src="images/midterm_review_slide-29.png" alt="Slide 29: Floating-point addition and subtraction error analysis" width="48%" />
</p>

$$
relative\ error = \frac{\left|\mathrm{output} - \mathrm{reference}\right|}{\mathrm{reference}}
$$

- **Numerical error:** Finite significand precision can require rounding even when both inputs are exactly representable. The checker measures output error relative to the higher-precision result for the already encoded inputs; it does not measure representation error introduced when original real-valued inputs were encoded. Both FLP checkers print an average of the error defined above. Because the denominator is not absolute-valued and zero references are not handled explicitly, this statistic can be negative or invalid. It is diagnostic only; pass/fail uses the encoding checks above.
- **Cancellation and precision:** Opposite-sign addition can cancel leading digits, making small absolute errors large relative to a near-zero result. Guard bits and wider intermediate values help preserve information before final rounding, but the checker does not separately report cancellation cases or derive error bounds. Its average covers independent test cases, not error accumulation through dependent arithmetic operations; pipeline stages partition a single operation.


## 6. Future Improvements

### 6.1 FLP add: Dual Datapaths

<p align="center">
    <img src="images/midterm_review_slide-19.png" alt="Slide 19: Near and far normalization cases, with corrected far-case equations" width="48%" />
    <img src="images/midterm_review_slide-20.png" alt="Slide 20: Floating-point adder with dual datapaths" width="48%" />
</p>

The current adder places a full alignment shifter and a full normalization shifter in one datapath. A dual-datapath design separates the near case, $|e_a-e_b| \le 1$, from the far case, $|e_a-e_b| \ge 2$. The near path needs at most one bit of alignment but may require a large normalization left shift after cancellation. The far path needs arbitrary alignment, but normalization shifts by at most one bit. Each path therefore needs only one general-purpose shifter, potentially reducing the serial delay from roughly $2T_{\mathrm{shift}}+T_{\mathrm{add}}$ to $T_{\mathrm{shift}}+T_{\mathrm{add}}$. The extra arithmetic, control, and output selection increase area and must be included when evaluating the timing benefit.

### 6.2 FLP add: Alignment only for $-25 \le e_a-e_b \le 25$

<p align="center">
    <img src="images/midterm_review_slide-22.png" alt="Slide 22: Limiting floating-point alignment by exponent difference" width="80%" />
</p>

A large-exponent-difference bypass could reduce alignment logic or switching activity. For normalized significands with $|m_a|,|m_b|<2$, an exponent difference $d=e_a-e_b \ge 26$ makes the aligned smaller operand satisfy $|m_b2^{-d}|<2^{-25}$. Even after a possible one-bit normalization left shift, its contribution remains below $2^{-24}$. The same argument applies with the operands reversed, motivating explicit alignment only for $|e_a-e_b|\le25$ under the homework's finite-precision approximation. The boundary at 25 must be retained. The current RTL shifts for every exponent difference; implementing a bypass would require checking negative-operand sign extension and rounding boundaries. A small contribution can still affect rounding near a midpoint, so this cutoff alone does not guarantee correctly rounded IEEE 754 results.

### 6.3 FLP mul: Integration of Rounding

The current multiplier performs significand multiplication, normalization, rounding, and possible renormalization in sequence. In multiplier architectures that produce the lower product bits early, rounding information can be available before the upper product bits and the normalization decision are complete. Rounding could be integrated into upper-product generation by preparing two candidate versions for the possible normalization outcomes, then selecting the appropriate result once the leading product bit is known. This can reduce the rounding work left after multiplication, at the cost of extra candidate-generation logic, selection, and routing. The synthesized timing must confirm a benefit: the RTL multiplication operator does not guarantee early availability of the lower bits, and the selection logic may offset the saved delay.

## 7. Result

### 7.1 Summary
![Area, delay, latency, and power summary][1]

Observations:

1. **Area–delay tradeoff:** Lower delays were obtained under the Delay configuration at the cost of increased area. For example, the combinational FXP multiplier changes from 918.190 µm² and 1.9950 ns under Area to 1492.163 µm² and 0.3320 ns under Delay. Intermediate area and delay values were obtained under Between.
2. **Power tradeoff:** Higher estimated power was reported under Delay than under Area for every design. For the combinational FXP multiplier, total power increases from approximately 0.000552 W to 0.006368 W. These values were estimated under the synthesis tool's activity assumptions.
3. **Register overhead:** Additional sequential area was required by pipelining. The seven-stage adder has 263.140 µm² of sequential area, compared with 131.570 µm² for the four-stage adder. Under Delay, their summary delays are close—0.2452 and 0.2460 ns—so the extra stages provide little improvement in the reported delay while increasing register area and the reported latency.

### 7.2 Pipelining
![Per-stage pipeline delays and critical stages][2]

Observations:

1. **Seven-stage adder:** The largest arrival time is reported in Stage 5 (normalization) under Area and Delay, at 0.9631 and 0.2461 ns, respectively. Under Between, Stage 6 (rounding/renormalization) has the largest arrival time, at 0.6051 ns.
2. **Four-stage adder:** The largest arrival time is reported in Stage 3 (normalization) under Area, Stage 4 (rounding/packing) under Delay, and Stage 1 (unpacking/alignment) under Between. Their respective delays are 0.9909, 0.2610, and 0.6165 ns. Under Delay, the four stages are relatively balanced, ranging from 0.2460 to 0.2610 ns.
3. **Three-stage multiplier:** Stage 2 (multiplication) dominates under Area and Between, at 1.8572 and 1.1122 ns. Under Delay, Stage 3 (normalization/rounding/packing) becomes the largest-delay stage at 0.3847 ns, slightly exceeding Stage 2 at 0.3713 ns. Stage 1 remains much shorter, at 0.0448 ns in all three configurations, showing that equal stage counts do not imply equal stage delays.

## 8. Conclusion

From the results, it was observed that adding pipeline stages does not guarantee a proportional improvement in performance. In particular, similar delays were obtained for the four-stage and seven-stage adders under the Delay configuration, despite the greater register area and reported latency of the seven-stage design. The per-stage results also showed that the bottleneck can move as synthesis constraints are tightened: multiplication dominated the three-stage multiplier under Area, whereas normalization and rounding became the largest-delay stage under Delay. Therefore, pipeline effectiveness depends on the balance of measured stage delays as well as the stage count. Finally, the unresolved SDF diagnostic demonstrated that successful synthesis alone is insufficient to establish successful post-sim verification.

Going forward, choose pipeline boundaries from measured stage timing rather than dividing the operation sequence into a fixed number of stages alone. Register overhead and the alignment of sign, exponent, and zero information should be considered alongside the delay reduction. Further circuit optimization could have been included if time perimitted.

[1]: images/summary_table.png
[2]: images/pipeline_table.png
