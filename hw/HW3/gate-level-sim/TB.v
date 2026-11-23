`timescale 1ns/1ps

// Self-contained Verilog-2001 stimulus and scoreboard for RTL or gate simulation.
// vectors.txt supplies Q5.50 architectural answers, not exact fused answers.
// This TB does not calculate floating-point arithmetic internally.
// Define DUT_MODULE and DUT_LATENCY together for a pipelined design.
`ifndef DUT_MODULE
`define DUT_MODULE DP4
`endif

`ifndef DUT_LATENCY
`define DUT_LATENCY 0
`endif

`ifndef TB_PERIOD
`define TB_PERIOD 20.0
`endif

`ifndef HW3_SDF_FILE
`define HW3_SDF_FILE "unconfigured.sdf"
`endif

module testbench;

// Compile-time defaults: DP4 is combinational; pipelines use four capture edges.
// +LATENCY=N can override the compiled latency at runtime.
parameter integer DEFAULT_LATENCY = `DUT_LATENCY;
parameter real PERIOD = `TB_PERIOD;

// DUT ports: precision=0 selects FP32, precision=1 selects upper-half FP16.
reg clk, rst, precision;
reg [31:0] x1,x2,x3,x4,y1,y2,y3,y4;
wire [31:0] z;

// File parsing, cycle accounting, scoreboard pointers, and result counters.
integer result_fd, reset_checks;
integer fd, status, mode, latency, cycle, wr, rd, count, errors, checked;

// Each queued transaction retains its due cycle, ID, precision, expected result,
// and original operands. wr is the next write slot; rd is the next check slot.
integer due [0:65535];
integer ids [0:65535];
reg modes [0:65535];
reg [31:0] expected [0:65535];
reg [255:0] operands [0:65535];

// One vector row: mode, x1..x4, y1..y4, and expected output.
reg [31:0] a,b,c,d,e,f,g,h,golden;
reg [4095:0] vector_path, vcd_path, sdf_path, result_path;
reg write_result;
reg gate_mode;

// Free-running clock; PERIOD is in ns because of the timescale above.
always #(PERIOD/2.0) clk = ~clk;

// Recognize either format's NaN encoding without checking its payload.
// FP16 sign/exponent/fraction are in bits [31], [30:26], and [25:16].
function is_nan;
    input [31:0] value;
    input half;
    begin
        if (half) is_nan = (value[30:26] == 5'b11111) && (value[25:16] != 0);
        else is_nan = (value[30:23] == 8'hff) && (value[22:0] != 0);
    end
endfunction

// Check one result. FP16 lower bits are unspecified and are ignored.
// id=-1 identifies a reset-output check rather than an input-vector row.
task compare;
    input [31:0] want;
    input half;
    input integer id;
    input [255:0] data;
    reg bad;
    begin
        // Reject X/Z in every checked bit before testing NaN or equality.
        if (half) bad = (^z[31:16] === 1'bx);
        else bad = (^z === 1'bx);

        // Any NaN payload is acceptable when the golden result is NaN.
        if (!bad) begin
            if (is_nan(want,half)) bad = !is_nan(z,half);
            else if (half) bad = (z[31:16] !== want[31:16]);
            else bad = (z !== want);
        end

        // Count every mismatch, but limit detailed logging to the first 20.
        checked = checked + 1;
        if (bad) begin
            errors = errors + 1;
            if (errors <= 20)
                $display("FAIL id=%0d cycle=%0d FP16=%0d operands=%064h expected=%08h actual=%08h",
                         id,cycle,half,data,want,z);
        end
    end
endtask

// Reuse the falling edge reached by tick instead of skipping a clock cycle.
// All result comparisons finish before this process drives the next inputs.
task drive_phase;
    begin
        if (clk !== 1'b0) @(negedge clk);
    end
endtask

// Advance to a rising edge and allow half a period for the final combinational
// stage and gate delays before checking the next queued result.
task tick;
    begin
        @(posedge clk);
        #(PERIOD/2.0);
        cycle = cycle + 1;

        // Fixed-latency traffic produces at most one expected result per cycle.
        if (rd < wr && due[rd] <= cycle) begin
            compare(expected[rd],modes[rd],ids[rd],operands[rd]);
            rd = rd + 1;
        end
    end
endtask

// Feed zeros while checking outstanding transactions. These extra inputs
// are not queued; only previously submitted vectors contribute result checks.
task drain;
    begin
        while (rd < wr) begin
            drive_phase;
            precision=0; x1=0; x2=0; x3=0; x4=0;
            y1=0; y2=0; y3=0; y4=0;
            tick;
        end
    end
endtask

// Caller drains the scoreboard before reset, so no queued results are lost.
// Assert/deassert on falling edges and hold active-high reset for two cycles.
task reset_dut;
    begin
        drive_phase;
        rst=1; precision=0;
        x1=0; x2=0; x3=0; x4=0; y1=0; y2=0; y3=0; y4=0;

        repeat (2) tick;

        // All pipeline registers should reset to zero; DP4 has no pipeline.
        if (latency > 0) begin
            compare(0,0,-1,0);
            reset_checks = reset_checks + 1;
        end
        drive_phase; rst=0;
    end
endtask

// Main stimulus process: initialize, configure optional features, and run rows.
initial begin
    clk=0; rst=1; precision=0;
    x1=0; x2=0; x3=0; x4=0; y1=0; y2=0; y3=0; y4=0;
    reset_checks=0;
    cycle=0; wr=0; rd=0; count=0; errors=0; checked=0;

    // A result record is written only after all vectors and queued checks finish.
    // The runner removes the old file before compile, so early exits stay incomplete.
    write_result=$value$plusargs("RESULT=%s",result_path);

    // Runtime latency is measured in capture edges; input capture is edge one.
    latency=DEFAULT_LATENCY;
    status=$value$plusargs("LATENCY=%d",latency);
    if (latency < 0 || latency > 1000) begin
        $display("FAIL: LATENCY must be 0..1000"); $finish;
    end

    // Default paths assume launch from the directory containing HW3.
    vector_path="HW3/testdata/vectors.txt";
    status=$value$plusargs("VECTORS=%s",vector_path);

    // Compile SDF support only for gate runs. VCS requires literal file names;
    // the runner copies this work-directory report to the published report path.
    gate_mode=$test$plusargs("GATE");
`ifdef HW3_GATE_SIM
    if ($value$plusargs("SDF=%s",sdf_path)) begin
        if (!gate_mode) begin $display("FAIL: SDF requires +GATE"); $finish; end
        $display("HW3_SDF_ANNOTATION_REQUESTED");
        if (sdf_path != `HW3_SDF_FILE) begin
            $display("FAIL: +SDF differs from compiled HW3_SDF_FILE"); $finish;
        end
        $sdf_annotate(`HW3_SDF_FILE,dut,,"sdf_annotation.rpt","MAXIMUM");
    end else if (gate_mode) begin
        $display("FAIL: +GATE requires +SDF=<file>"); $finish;
    end
`else
    if (gate_mode || $value$plusargs("SDF=%s",sdf_path)) begin
        $display("FAIL: gate simulation requires HW3_GATE_SIM at compile time");
        $finish;
    end
`endif

    // Optional activity dump includes testbench/dut for PrimeTime strip_path.
    if ($value$plusargs("VCD=%s",vcd_path)) begin
        $dumpfile(vcd_path); $dumpvars(0,testbench);
    end

    // Abort on a missing vector file instead of reporting an empty PASS.
    fd=$fopen(vector_path,"r");
    if (!fd) begin $display("FAIL: cannot open %0s",vector_path); $finish; end

    // Put a special transaction in flight before reset. This checks that all
    // banks clear even when NaN metadata and stale arithmetic are present.
    if (latency > 0) begin
        drive_phase;
        rst=0; precision=0;
        x1=32'h7fc00000; y1=32'h3f800000;
        repeat (latency) tick;
    end
    reset_dut;

    // Each row has one decimal precision field and nine hexadecimal words.
    while (!$feof(fd)) begin
        status=$fscanf(fd,"%d %h %h %h %h %h %h %h %h %h\n",
                       mode,a,b,c,d,e,f,g,h,golden);

        if (status != 10) begin
            $display("FAIL: malformed vector after case %0d, fields=%0d",count,status);
            $finish;
        end
        if ((mode != 0 && mode != 1) || wr >= 65536) begin
            $display("FAIL: invalid precision or scoreboard capacity exceeded"); $finish;
        end

        // Drive on the opposite edge to avoid racing the DUT input registers.
        drive_phase;
        precision=mode;
        x1=a; x2=b; x3=c; x4=d;
        y1=e; y2=f; y3=g; y4=h;

        if (latency == 0) begin
            // Combinational DP4: wait for settling and compare immediately.
            #(PERIOD/2.0);
            compare(golden,precision,count,{a,b,c,d,e,f,g,h});
            cycle = cycle + 1;
        end else begin
            // Pipeline: schedule the check and preserve this transaction's mode.
            // Latency=4 checks half a period after the fourth capture edge.
            expected[wr]=golden; modes[wr]=precision; ids[wr]=count;
            operands[wr]={a,b,c,d,e,f,g,h}; due[wr]=cycle+latency;
            wr=wr+1;
            tick;
        end

        count=count+1;

        // Finish in-flight operations, then test reset between traffic blocks.
        if (count == 1000) begin drain; reset_dut; end
    end

    // Finish the tail of the pipeline before producing the final summary.
    $fclose(fd);
    drain;
    if (count == 0) begin $display("FAIL: empty vector file"); $finish; end

    // Write a small JSON record using Verilog-2001 file operations. No record
    // is produced for malformed input, missing files, or watchdog termination.
    if (write_result) begin
        result_fd=$fopen(result_path,"w");
        if (!result_fd) begin
            $display("FAIL: cannot write result record"); $finish;
        end
        if (errors == 0) $fdisplay(result_fd,"{\"status\":\"passed\",");
        else $fdisplay(result_fd,"{\"status\":\"failed\",");
        $fdisplay(result_fd,"\"vectors\":%0d,\"checked\":%0d,\"errors\":%0d,",count,checked,errors);
        $fdisplay(result_fd,"\"latency\":%0d,\"reset_checks\":%0d,\"period_ns\":%0.9f}",latency,reset_checks,PERIOD);
        $fclose(result_fd);
    end

    // Verilog-2001 $finish has no portable failure exit status; inspect the log.
    if (errors == 0) $display("PASS: %0d vectors, %0d checks",count,checked);
    else $display("FAIL: %0d mismatches in %0d checks",errors,checked);
    $finish;
end

// Independent watchdog catches a hung stimulus process or missing progress.
initial begin
    #(PERIOD*200000);
    $display("FAIL: watchdog timeout");
    $finish;
end

// Keep the instance name dut stable for SDF annotation and VCD hierarchy.
`DUT_MODULE dut (
    .clk(clk), .rst(rst), .precision(precision),
    .x1(x1), .x2(x2), .x3(x3), .x4(x4),
    .y1(y1), .y2(y2), .y3(y3), .y4(y4), .z(z)
);
endmodule
