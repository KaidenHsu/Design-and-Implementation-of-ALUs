# DP4 RTL Design

## 1. Datapath And Interfaces

The [combinational top](../rtl/DP4.v) and [pipeline top](../rtl/DP4_pipe.v) each contain the complete inline datapath, following the [schematic](../images/schematics.jpg). Neither top instantiates helper modules or requires a shared RTL source. Port names and widths are unchanged. `precision=0` selects FP32; `precision=1` selects IEEE FP16 in port bits `[31:16]`. FP16 output bits `[15:0]` are zero.

| Stage | Arithmetic | Pipeline Boundary |
|---|---|---|
| 1 | Unpack eight operands, prepare 12-bit multiplier halves, classify values, compute biased product exponents and alignment differences. | S1: 275 bits. |
| 2 | Four 24-bit significand multipliers, Q2.50 magnitude alignment, then 55-bit signed conversion. | S2: 263 bits. |
| 3 | Balanced adder tree of four signed Q5.50 products. | S3: 98 bits. |
| 4 | Sign/magnitude conversion, leading-one detection, exponent adjustment, and normalization or subnormal shifting. | S4: 100 bits. |
| 5 | Nearest-even rounding, carry renormalization, range checks, packing, and priority result selection. | Combinational output. |

The five rows describe arithmetic sections, not five cycles in the [combinational top](../rtl/DP4.v). Its sections are connected by wires, with no clocked blocks or internal pipeline registers. Changes to input operands or precision propagate to the output after the complete combinational delay; `clk` and `rst` do not control this result. Calling it single-cycle requires a surrounding register-to-register clock period that accommodates that delay.

Bundle widths include precision, signs, value classifications, and exponent metadata. The pipeline accepts one transaction per cycle and produces its corresponding result after four capture edges plus stage-5 settling. Synchronous active-high reset clears all four banks. The combinational top ignores clock and reset for arithmetic.

## 2. Finite-Width Arithmetic

Normal significands include the hidden one; zero/subnormal significands do not. FP32 uses 24 significand bits. FP16 places its 11-bit significand in the low multiplier half. Each shared multiplier consists of four unsigned 12x12 partial multipliers; FP16 selects only the low-low product.

For exponent fields, replace zero by one and compute `ep = ex_eff + ey_eff - bias`, where the bias is 127 or 15. Product exponents and `emax` are signed 10-bit values. The maximum excludes zero and exceptional products; an all-zero finite group uses a deterministic zero anchor. `ediff` is nonnegative.

FP32 products use Q2.46 and append four fractional zeros. FP16 products use Q2.20 and append 30 fractional zeros. Both then shift logically right into a 52-bit Q2.50 magnitude, discarding low bits. Extend to 55 bits before applying the product sign. Accumulation uses signed Q5.50 and does not round intermediate sums.

Alignment truncation is intentional. A tiny term can disappear before larger products cancel. The [architectural model](../testdata/generate_vectors.py) reproduces this behavior; the separate exact fused model retains every product bit. No numerical tolerance is used for architectural pass/fail comparisons.

## 3. Normalization And Output Selection

For a nonzero sum with leading one at position `k`, signed 7-bit `adj_norm = k - 50`. Add its sign extension to `emax`; do not subtract the format bias again. Shift the magnitude so its ordinary normalized hidden one is at bit 53.

If the resulting biased exponent is zero or negative, shift right by `1 - exponent` before rounding. Jam discarded bits into the low bit to preserve final rounding information and use exponent field zero. Exponent calculation precedes this shift, avoiding feedback between the normalization shift and its own exponent correction.

| Format | Retained Bits | Guard | Round | Sticky |
|---|---|---|---|---|
| FP32 | `[53:30]` | `[29]` | `[28]` | OR of `[27:0]`. |
| FP16 | `[53:43]` | `[42]` | `[41]` | OR of `[40:0]`. |

Increment when `guard && (round || sticky || retained_lsb)`. Preserve a carry bit, shift once on carry, and increase the exponent. A rounded subnormal may become the smallest normal. Finite overflow produces signed infinity, while tiny negative results that round to zero retain their sign.

Priority selection handles NaN operands, invalid zero-times-infinity products, and opposite infinite products before ordinary infinity propagation or finite overflow/zero. NaN output is canonical quiet NaN; exception flags are not exposed. Exact cancellation of aligned terms produces positive zero, except four genuinely negative-zero products preserve negative zero. Original operand classifications distinguish zero products from products lost during alignment.

## 4. Verification

Generate vectors and run independent reference checks before RTL simulation:

```sh
$ python3 -B testdata/test_reference.py
$ python3 -B testdata/generate_vectors.py
$ python3 -B scripts/test_rtl.py
```

The [local regression](../scripts/test_rtl.py) requires Icarus Verilog and `vvp`. It compiles strict Verilog-2001, exercises all three tops through both copies of the testbench, adds 6,000 deterministic randomized cases, and checks that an intentionally incorrect latency fails. Temporary builds do not publish course EDA results.

The default [RTL testbench](../rtl-sim/TB.v) checks 2,048 architectural vectors with alternating precision and poisoned FP16 lower input bits. It fills pipeline banks with NaN traffic before reset and resets again between scored blocks. Pipeline samples occur half a period after the fourth capture edge, before the next input drive; combinational samples occur half a period after input drive. Final-stage timing must meet this half-period observation budget.

The [course automation](automation.md) compiles each top directly; no helper RTL file is required. The [clock-gated top](../rtl/DP4_CG.v) uses the same arithmetic and pipeline boundaries with an additional clock-gated FP32 operand bank. Local RTL simulation does not establish Design Compiler compatibility, post-synthesis timing, SDF annotation coverage, or power savings.

## Clock-Gated Operand Bank

The [clock-gated top](../rtl/DP4_CG.v) keeps the low-low multiplier on the ordinary
S1 operand path. A dedicated 192-bit FP32 operand bank supplies both inputs of
the high-low, low-high, and high-high multipliers. Retaining low halves as well
as upper halves prevents new FP16 operands from toggling cross products.
The existing precision-controlled product mux ignores retained FP32 products in
FP16 mode; gated registers hold their previous values rather than becoming zero.

A low-phase latch captures `rst || !precision`, and its output gates `clk`.
FP32 input transactions update the retained bank on the same capture edge as
S1; FP16 transactions hold it. Synchronous reset opens the gate and clears the
retained operands even in FP16 mode. Metadata and downstream banks remain on
the ordinary clock so precision changes do not stall in-flight transactions.
The combined product adder can still toggle because the low-low product changes.
Local stability checks establish gating behavior, not physical power savings.
The existing synthesis flow runs `replace_clock_gates`; its clock-gating report,
gate simulation, and PrimeTime activity analysis must establish mapping and power.
