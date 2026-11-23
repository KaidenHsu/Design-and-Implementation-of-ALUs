"""Simulate the schematic datapaths with Icarus Verilog and architectural answers.

Run from any directory with Python 3, iverilog, and vvp in PATH. Every build,
temporary vector file, and simulator result stays in a disposable directory.
These are RTL checks, not synthesis or gate-level timing validation.
"""

import json
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'testdata'))

from generate_vectors import compute_architectural_dot_product, create_edge_cases, read_inputs


@unittest.skipUnless(shutil.which('iverilog') and shutil.which('vvp'),
                     'Icarus Verilog and vvp are required')
class RTLTests(unittest.TestCase):
    """Check arithmetic, continuous mixed-mode traffic, reset, and latency."""

    def simulate(self, rows, top, latency, bench='rtl-sim', should_pass=True):
        """Compile one actual top and check every architectural result and reset."""
        with tempfile.TemporaryDirectory(prefix='hw3-rtl-') as temporary:
            work = Path(temporary)
            vectors = work / 'vectors.txt'
            lines = []
            for half, values in rows:
                # Nonzero ignored lower halves must not affect FP16 arithmetic.
                ports = [(v << 16) | (0x5a00 + i) if half else v
                         for i, v in enumerate(values)]
                want = compute_architectural_dot_product(values, half) << (16 if half else 0)
                lines.append(str(int(half)) + ' ' +
                             ' '.join(f'{v:08x}' for v in ports + [want]))
            vectors.write_text('\n'.join(lines) + '\n')

            # Keep compilation strict Verilog-2001, matching the course flow.
            executable = work / 'simv'
            command = ['iverilog', '-g2001', '-Wall', '-s', 'testbench',
                       '-DDUT_MODULE=' + top, '-DDUT_LATENCY=' + str(latency),
                       '-o', str(executable), str(ROOT / bench / 'TB.v'),
                       str(ROOT / 'rtl' / (top + '.v'))]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)

            completion = work / 'completion.json'
            result = subprocess.run(['vvp', str(executable), '+VECTORS=' + str(vectors),
                                     '+RESULT=' + str(completion)], capture_output=True,
                                    text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(completion.is_file(), result.stdout + result.stderr)
            record = json.loads(completion.read_text())

            if should_pass:
                self.assertIn('PASS:', result.stdout, result.stdout)
                self.assertNotIn('FAIL', result.stdout, result.stdout)
                self.assertEqual(record['status'], 'passed')
                self.assertEqual(record['vectors'], len(rows))
                self.assertEqual(record['latency'], latency)
                resets = (1 + int(len(rows) >= 1000)) if latency else 0
                self.assertEqual(record['reset_checks'], resets)
                self.assertEqual(record['checked'], len(rows) + resets)
            else:
                self.assertEqual(record['status'], 'failed', result.stdout)
                self.assertGreater(record['errors'], 0)

    def test_supplied_and_directed(self):
        """Verify all tops and both benches against all supplied/directed rows."""
        rows = {half: read_inputs(ROOT / 'testdata' /
                                 ('FP16.txt' if half else 'FP32.txt'), half)
                + create_edge_cases(half) for half in (False, True)}
        sequence = [(half, rows[half][i]) for i in range(len(rows[False]))
                    for half in (False, True)]
        for bench in ('rtl-sim', 'gate-level-sim'):
            for top, latency in [('DP4', 0), ('DP4_pipe', 4), ('DP4_CG', 4)]:
                with self.subTest(top=top, bench=bench):
                    self.simulate(sequence, top, latency, bench=bench)

    def test_randomized_alignment_and_rounding(self):
        """Stress finite/special values, cancellation, and stage-aligned precision."""
        rng = random.Random(7813)
        sequence = []
        for i in range(6000):
            half = bool(i & 1)
            eb, fb = (5, 10) if half else (8, 23)
            sign = 1 << (eb + fb)
            bias = (1 << (eb - 1)) - 1
            one = bias << fb

            # Mix unrestricted encodings with close exponents and cancellation.
            values = [rng.getrandbits(1 + eb + fb) for _ in range(8)]
            if i % 3 == 0:
                values = [(rng.randrange(max(1, bias - 8), bias + 9) << fb) |
                          rng.getrandbits(fb) | (rng.randrange(2) * sign)
                          for _ in range(8)]
            if i % 5 == 0:
                values[1] = values[0] ^ sign
                values[5] = values[4]
                values[6] = one
            sequence.append((half, values))

        for top, latency in [('DP4', 0), ('DP4_pipe', 4), ('DP4_CG', 4)]:
            with self.subTest(top=top):
                self.simulate(sequence, top, latency)

    def test_clock_gated_operands_hold(self):
        """Check FP16 operand retention, quiet partial products, and safe clock gating."""
        bench = '''
`timescale 1ns/1ps
module gating_test;
    reg clk = 0;
    reg rst = 1;
    reg precision = 0;
    reg [31:0] x = 32'h3fc12345;
    reg [31:0] y = 32'h40123456;
    wire [31:0] z;
    integer edges = 0;
    integer saved_edges;
    integer i;
    reg [191:0] saved_operands;
    reg checking = 0;

    DP4_CG dut(clk, rst, precision, x, x, x, x, y, y, y, y, z);
    always #5 clk = ~clk;
    always @(posedge dut.fp32_clk) edges = edges + 1;

    // All three unused products must remain unchanged, for every lane.
    genvar lane;
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : monitors
            wire [71:0] products = {dut.stage2.lanes[lane].p_lh,
                                   dut.stage2.lanes[lane].p_hl,
                                   dut.stage2.lanes[lane].p_hh};
            reg [71:0] saved;
            always @(negedge clk) begin
                if (!checking) saved = products;
                else if (products !== saved) $fatal(1, "Unused multiplier toggled");
            end
        end
    endgenerate

    initial begin
        // Initialize through synchronous reset, then retain a nonzero FP32 bank.
        @(negedge clk); rst = 0;
        @(negedge clk); precision = 1;
        saved_operands = {dut.fp32_mx, dut.fp32_my};
        saved_edges = edges;
        #1; checking = 1;

        for (i = 0; i < 16; i = i + 1) begin
            @(negedge clk);
            x = x + 32'h00010001;
            y = y + 32'h00020003;
            @(posedge clk); #1;
            if ({dut.fp32_mx, dut.fp32_my} !== saved_operands)
                $fatal(1, "FP16 changed gated operands");

            // A precision pulse while clk is high must not create a gated edge.
            precision = 0; #1; precision = 1; #1;
            if (edges != saved_edges) $fatal(1, "Spurious gated clock edge");
        end

        // FP16 reset must reopen the gate and clear retained operands.
        @(negedge clk); checking = 0; rst = 1;
        @(posedge clk); #1;
        if ({dut.fp32_mx, dut.fp32_my} !== 192'd0)
            $fatal(1, "Gated synchronous reset failed");
        $display("PASS: gated operands and partial products hold");
        $finish;
    end
endmodule
'''
        with tempfile.TemporaryDirectory(prefix='hw3-gating-') as temporary:
            work = Path(temporary)
            source = work / 'gating_test.v'
            source.write_text(bench)
            executable = work / 'simv'
            result = subprocess.run(['iverilog', '-g2001', '-s', 'gating_test',
                                     '-o', str(executable), str(source),
                                     str(ROOT / 'rtl/DP4_CG.v')],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(['vvp', str(executable)], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: gated operands', result.stdout)

    def test_clock_gated_precision_bursts(self):
        """Check long FP16 holds and FP32 resumption against architectural answers."""
        rng = random.Random(2311)
        sequence = []
        for half in (False, True, True, False, True, False):
            width = 16 if half else 32
            for i in range(64):
                values = [rng.getrandbits(width) for _ in range(8)]
                sequence.append((half, values))
        self.simulate(sequence, 'DP4_CG', 4)

    def test_wrong_pipeline_latency_is_rejected(self):
        """Prove that the scoreboard detects a one-cycle pipeline mismatch."""
        one = 0x3f800000
        sequence = [(False, [one + (i << 16), 0, 0, 0] + [one] * 4)
                    for i in range(32)]
        self.simulate(sequence, 'DP4_pipe', 5, should_pass=False)

    def test_sdf_compilation_is_gate_only(self):
        """Keep annotation out of RTL builds and compile a literal gate log path."""
        with tempfile.TemporaryDirectory(prefix='hw3-sdf-') as temporary:
            work = Path(temporary)
            for bench in ('rtl-sim', 'gate-level-sim'):
                for gate in (False, True):
                    flags = ['-DHW3_GATE_SIM=1'] if gate else []
                    preprocessed = work / 'preprocessed.v'
                    command = ['iverilog', '-g2001', '-E', '-o', str(preprocessed)]
                    command += flags + [str(ROOT / bench / 'TB.v'), str(ROOT / 'rtl/DP4.v')]
                    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    text = preprocessed.read_text()
                    self.assertEqual('$sdf_annotate(' in text, gate)
                    if gate:
                        self.assertIn('$sdf_annotate("unconfigured.sdf",dut,,"sdf_annotation.rpt",', text)

            # Exercise syntax of the gate-only branch, not actual SDF timing.
            executable = work / 'simv'
            result = subprocess.run(['iverilog', '-g2001', '-gspecify',
                                     '-DHW3_GATE_SIM=1', '-s', 'testbench',
                                     '-o', str(executable), str(ROOT / 'gate-level-sim/TB.v'),
                                     str(ROOT / 'rtl/DP4.v')], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(['vvp', str(executable), '+GATE'],
                                    capture_output=True, text=True, timeout=30)
            self.assertIn('FAIL: +GATE requires +SDF=<file>', result.stdout)


if __name__ == '__main__':
    unittest.main()
