`timescale 1ns/1ps

// Read local normal/ inputs, or use +DATA_DIR=path for shared inputs.
// Plain Verilog-2001. FILE_ANS is the DUT output file, not the golden file.
// Check 200 input triples as signed two's-complement fixed-point operands.
`ifndef CYCLE
`define CYCLE 100
`endif
`define FILE_A "normal/a.txt"
`define FILE_B "normal/b.txt"
`define FILE_C "normal/c.txt"
`define FILE_ANS "ans_normal.txt"

// Define SDF_FILE as a quoted path at compile time to enable post-sim timing.
// Leave SDF_FILE undefined for pre-sim. All simulation copies are identical.

module testbench;

    // Shared input directory; all generated outputs stay in the run directory.
    reg [1023:0] data_directory;
    reg [2047:0] input_path;
    integer data_directory_option;

    reg [31:0] input_a, input_b, input_c;
    wire [31:0] outcome;

    reg [31:0] data_a [0:199];
    reg [31:0] data_b [0:199];
    reg [31:0] data_c [0:199];

    reg signed [63:0] golden_product, golden_sum;
    reg [31:0] answer;
    integer file_a, file_b, file_c, file_ans, report;
    integer garbage_a, garbage_b, garbage_c, i;
    integer checked, errors, total_checked, total_errors;
    integer result_file, normal_checked, normal_errors;
    reg [31:0] extra;

    FXP_FMA test_module (
        .a(input_a), .b(input_b), .c(input_c), .d(outcome)
    );

`ifdef SDF_FILE
    initial begin
        $display("HW2_SDF_ANNOTATION_REQUESTED %s", `SDF_FILE);
        // Omit the optional config argument; an empty string is a filename in VCS.
        $sdf_annotate(`SDF_FILE, test_module, , "sdf_annotation.log", "MAXIMUM");
    end
`endif

    // Keep a waveform for both RTL and gate-level verification.
    // FSDB requires the course VCS/Verdi integration; VCD is the default.
    initial begin
`ifdef HW2_FSDB
        $fsdbDumpfile("wave.fsdb");
        $fsdbDumpvars(0, testbench);
        $fsdbDumpMDA();
`else
        $dumpfile("wave.vcd");
        $dumpvars(0, testbench);
`endif
    end

    // Reuse HW1's file-driven signed arithmetic and exact result comparison.
    // No generated random patterns, real conversion, or shift/add oracle.
    // The provided A/B/C hex words are treated as signed integers for FXP.
    // Their FLP ab+c.txt files are not FXP golden answers and are not used.
    task run_dataset;
        input [63:0] name;
        input integer count;
        input [1023:0] path_a, path_b, path_c, path_ans, path_report;
        begin
            $sformat(input_path, "%0s/%0s", data_directory, path_a);
            file_a = $fopen(input_path, "r");
            $sformat(input_path, "%0s/%0s", data_directory, path_b);
            file_b = $fopen(input_path, "r");
            $sformat(input_path, "%0s/%0s", data_directory, path_c);
            file_c = $fopen(input_path, "r");
            if (!file_a || !file_b || !file_c) begin
                $display("ERROR: Cannot open %0s inputs", name);
                $finish;
            end

            for (i = 0; i < count; i = i + 1) begin
                garbage_a = $fscanf(file_a, "%h", data_a[i]);
                garbage_b = $fscanf(file_b, "%h", data_b[i]);
                garbage_c = $fscanf(file_c, "%h", data_c[i]);
                if (garbage_a != 1 || garbage_b != 1 || garbage_c != 1 ||
                    ^data_a[i] === 1'bx || ^data_b[i] === 1'bx || ^data_c[i] === 1'bx) begin
                    $display("ERROR: Invalid/missing %0s vector #%0d", name, i + 1);
                    $finish;
                end
            end
            garbage_a = $fscanf(file_a, "%h", extra);
            garbage_b = $fscanf(file_b, "%h", extra);
            garbage_c = $fscanf(file_c, "%h", extra);
            if (!$feof(file_a) || !$feof(file_b) || !$feof(file_c) ||
                garbage_a == 1 || garbage_b == 1 || garbage_c == 1) begin
                $display("ERROR: Expected exactly %0d records in %0s", count, name);
                $finish;
            end
            $fclose(file_a);
            $fclose(file_b);
            $fclose(file_c);

            file_ans = $fopen(path_ans, "w");
            report = $fopen(path_report, "w");
            if (!file_ans || !report) begin
                $display("ERROR: Cannot open %0s outputs", name);
                $finish;
            end

            checked = 0;
            errors = 0;
            for (i = 0; i < count; i = i + 1) begin
                input_a = data_a[i];
                input_b = data_b[i];
                input_c = data_c[i];

                // Widen the product and sign-extend C before the addition.
                // HW2 specifies the high 32 bits, rather than the full 64-bit sum.
                golden_product = $signed(input_a) * $signed(input_b);
                golden_sum = golden_product + $signed({{32{input_c[31]}}, input_c});
                answer = golden_sum[63:32];

                #(`CYCLE);
                checked = checked + 1;
                $fdisplay(file_ans, "%08h", outcome);

                if (answer !== outcome) begin
                    errors = errors + 1;
                    $display("FAIL #%0d A=%08h B=%08h C=%08h golden=%08h actual=%08h",
                             i+1, input_a, input_b, input_c, answer, outcome);
                    $fdisplay(report, "FAIL #%0d A=%08h B=%08h C=%08h golden=%08h actual=%08h",
                              i+1, input_a, input_b, input_c, answer, outcome);
                end else
                    $fdisplay(report, "PASS #%0d A=%08h B=%08h C=%08h golden=%08h actual=%08h",
                              i+1, input_a, input_b, input_c, answer, outcome);
            end

            $display("%0s: checked=%0d passed=%0d failed=%0d", name, checked, checked-errors, errors);
            $fdisplay(report, "SUMMARY %0s checked=%0d passed=%0d failed=%0d",
                      name, checked, checked-errors, errors);
            $fclose(file_ans);
            $fclose(report);
            if (name == "normal") begin
                normal_checked = checked;
                normal_errors = errors;
            end
            total_checked = total_checked + checked;
            total_errors = total_errors + errors;
        end
    endtask

    initial begin
        data_directory = ".";
        data_directory_option = $value$plusargs("DATA_DIR=%s", data_directory);

        input_a = 0;
        input_b = 0;
        input_c = 0;
        total_checked = 0;
        total_errors = 0;
        normal_checked = 0;
        normal_errors = 0;

        run_dataset("normal", 200, `FILE_A, `FILE_B, `FILE_C,
                    `FILE_ANS, "report_normal.txt");

        // Write a machine-readable completion record only after all comparisons.
        result_file = $fopen("tb_result.json", "w");
        if (!result_file) begin
            $display("ERROR: Cannot write tb_result.json");
            $finish;
        end
        if (total_errors == 0 && total_checked == 200)
            $fdisplay(result_file, "{\"status\":\"passed\",\"checked\":%0d,\"errors\":%0d,\"normal\":{\"checked\":%0d,\"errors\":%0d}}",
                      total_checked, total_errors, normal_checked, normal_errors);
        else
            $fdisplay(result_file, "{\"status\":\"failed\",\"checked\":%0d,\"errors\":%0d,\"normal\":{\"checked\":%0d,\"errors\":%0d}}",
                      total_checked, total_errors, normal_checked, normal_errors);
        $fclose(result_file);

        if (total_errors == 0 && total_checked == 200)
            $display("OVERALL PASS: all %0d vectors correct", total_checked);
        else
            $display("OVERALL FAIL: %0d errors in %0d vectors", total_errors, total_checked);
        $finish;
    end

endmodule

