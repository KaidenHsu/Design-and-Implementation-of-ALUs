`timescale 1ns/1ps

// Read local normal/ and special/ inputs, or use +DATA_DIR=path.
// Plain Verilog-2001. FILE_ANS is the DUT output file, not the golden file.
// The normal and special datasets are checked and reported separately.
`ifndef CYCLE
`define CYCLE 100
`endif
`define FILE_A "normal/a.txt"
`define FILE_B "normal/b.txt"
`define FILE_C "normal/c.txt"
`define FILE_ANS "ans_normal.txt"

`define SPECIAL_FILE_A "special/a.txt"
`define SPECIAL_FILE_B "special/b.txt"
`define SPECIAL_FILE_C "special/c.txt"
`define SPECIAL_FILE_ANS "ans_special.txt"

`define FILE_GOLDEN "normal/ab+c.txt"
`define SPECIAL_FILE_GOLDEN "special/ab+c.txt"

// Define SDF_FILE as a quoted path at compile time to enable post-sim timing.
// Leave SDF_FILE undefined for pre-sim. All simulation copies are identical.

module testbench;

    // Shared input directory; all generated outputs stay in the run directory.
    reg [1023:0] data_directory;
    reg [2047:0] input_path;
    integer data_directory_option;

    reg [31:0] input_a, input_b, input_c;
    wire [31:0] outcome;
    wire overflow;
    reg expected_overflow;

    reg [31:0] data_a [0:199];
    reg [31:0] data_b [0:199];
    reg [31:0] data_c [0:199];
    reg [31:0] golden [0:199];

    integer file_a, file_b, file_c, file_golden, file_ans, report;
    integer garbage_a, garbage_b, garbage_c, garbage_golden;
    integer i, index, checked, errors, total_checked, total_errors;
    integer result_file, normal_checked, normal_errors;
    integer special_checked, special_errors;
    integer normal_only, expected_checked;
    reg [31:0] extra;
    real real_answer, real_outcome, absolute_error, relative_error;
    real max_absolute, max_relative;

    FMA test_module (
        .a(input_a), .b(input_b), .c(input_c), .d(outcome),
        .overflow(overflow)
    );

    // Optional gate-level timing annotation, controlled only by SDF_FILE.
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

    // Decode finite binary32 values into Verilog real for diagnostic errors.
    // Unlike HW1's normal-only conversion, this also handles zero/subnormal values.
    // NaN and infinity are checked as raw bits and never passed to this function.
    function real fp_to_real;
        input [31:0] bits;
        integer exponent;
        real magnitude;
        begin
            if (bits[30:23] == 0)
                magnitude = $itor(bits[22:0]) * (2.0 ** (-149));
            else begin
                exponent = bits[30:23];
                exponent = exponent - 127;
                magnitude = (1.0 + $itor(bits[22:0]) / 8388608.0)
                          * (2.0 ** exponent);
            end
            fp_to_real = bits[31] ? -magnitude : magnitude;
        end
    endfunction

    // Like HW1, compare the expected bits with the DUT output after settling.
    // This helper only centralizes the reporting; no tolerance changes PASS/FAIL.
    task check_result;
        input integer vector_index;
        begin
            checked = checked + 1;
            $fdisplay(file_ans, "%08h", outcome);

            // Under ties-to-even, finite inputs with an infinite golden result
            // indicate arithmetic overflow. Infinite/NaN inputs do not.
            // Use the supplied golden result, never the DUT result, as the oracle.
            expected_overflow =
                (data_a[vector_index][30:23] != 8'hff) &&
                (data_b[vector_index][30:23] != 8'hff) &&
                (data_c[vector_index][30:23] != 8'hff) &&
                (golden[vector_index][30:23] == 8'hff) &&
                (golden[vector_index][22:0] == 23'd0);
            $fdisplay(report, "  overflow_expected=%b overflow_actual=%b",
                      expected_overflow, overflow);

            if ((outcome !== golden[vector_index]) ||
                (overflow !== expected_overflow)) begin
                errors = errors + 1;
                if (overflow !== expected_overflow)
                    $display("FAIL overflow #%0d expected=%b actual=%b",
                             vector_index + 1, expected_overflow, overflow);
                $display("FAIL #%0d A=%08h B=%08h C=%08h golden=%08h actual=%08h",
                         vector_index + 1, data_a[vector_index], data_b[vector_index],
                         data_c[vector_index], golden[vector_index], outcome);
                $fdisplay(report, "FAIL #%0d A=%08h B=%08h C=%08h golden=%08h actual=%08h",
                          vector_index + 1, data_a[vector_index], data_b[vector_index],
                          data_c[vector_index], golden[vector_index], outcome);
            end else begin
                $fdisplay(report, "PASS #%0d A=%08h B=%08h C=%08h golden=%08h actual=%08h",
                          vector_index + 1, data_a[vector_index], data_b[vector_index],
                          data_c[vector_index], golden[vector_index], outcome);
            end

            // Error statistics do not replace exact comparison. Relative error uses
            // |golden|, fixing HW1's negative denominator and zero-division problem.
            if ((^outcome !== 1'bx) &&
                outcome[30:23] != 8'hff && golden[vector_index][30:23] != 8'hff) begin
                real_answer = fp_to_real(golden[vector_index]);
                real_outcome = fp_to_real(outcome);
                absolute_error = real_outcome - real_answer;
                if (absolute_error < 0.0) absolute_error = -absolute_error;
                if (absolute_error > max_absolute) max_absolute = absolute_error;

                if (real_answer != 0.0) begin
                    relative_error = absolute_error / real_answer;
                    if (relative_error < 0.0) relative_error = -relative_error;
                    if (relative_error > max_relative) max_relative = relative_error;
                    $fdisplay(report, "  absolute_error=%e relative_error=%e",
                              absolute_error, relative_error);
                end else if (absolute_error == 0.0)
                    $fdisplay(report, "  absolute_error=0 relative_error=0");
                else
                    $fdisplay(report, "  absolute_error=%e relative_error=N/A (golden zero)",
                              absolute_error);
            end else
                $fdisplay(report, "  absolute_error=N/A relative_error=N/A (NaN/Inf/X/Z)");
        end
    endtask

    task run_dataset;
        input [63:0] name;
        input integer count;
        input [1023:0] path_a, path_b, path_c, path_golden, path_ans, path_report;
        begin
            $sformat(input_path, "%0s/%0s", data_directory, path_a);
            file_a = $fopen(input_path, "r");
            $sformat(input_path, "%0s/%0s", data_directory, path_b);
            file_b = $fopen(input_path, "r");
            $sformat(input_path, "%0s/%0s", data_directory, path_c);
            file_c = $fopen(input_path, "r");
            $sformat(input_path, "%0s/%0s", data_directory, path_golden);
            file_golden = $fopen(input_path, "r");
            if (!file_a || !file_b || !file_c || !file_golden) begin
                $display("ERROR: Cannot open %0s input/golden files", name);
                $finish;
            end

            // Read the provided hex vectors just as HW1 does. Golden answers are
            // already rounded FMA results; computing real_a*real_b+real_c here
            // could round differently and would not be a reliable FMA oracle.
            for (i = 0; i < count; i = i + 1) begin
                garbage_a = $fscanf(file_a, "%h", data_a[i]);
                garbage_b = $fscanf(file_b, "%h", data_b[i]);
                garbage_c = $fscanf(file_c, "%h", data_c[i]);
                garbage_golden = $fscanf(file_golden, "%h", golden[i]);
                if (garbage_a != 1 || garbage_b != 1 || garbage_c != 1 ||
                    garbage_golden != 1 || ^data_a[i] === 1'bx ||
                    ^data_b[i] === 1'bx || ^data_c[i] === 1'bx ||
                    ^golden[i] === 1'bx) begin
                    $display("ERROR: Invalid/missing %0s vector #%0d", name, i + 1);
                    $finish;
                end
            end

            // Reject extra records rather than silently skipping supplied inputs.
            garbage_a = $fscanf(file_a, "%h", extra);
            garbage_b = $fscanf(file_b, "%h", extra);
            garbage_c = $fscanf(file_c, "%h", extra);
            garbage_golden = $fscanf(file_golden, "%h", extra);
            if (!$feof(file_a) || !$feof(file_b) || !$feof(file_c) ||
                !$feof(file_golden) || garbage_a == 1 || garbage_b == 1 ||
                garbage_c == 1 || garbage_golden == 1) begin
                $display("ERROR: Expected exactly %0d records in %0s", count, name);
                $finish;
            end
            $fclose(file_a);
            $fclose(file_b);
            $fclose(file_c);
            $fclose(file_golden);

            file_ans = $fopen(path_ans, "w");
            report = $fopen(path_report, "w");
            if (!file_ans || !report) begin
                $display("ERROR: Cannot open %0s output files", name);
                $finish;
            end

            checked = 0;
            errors = 0;
            max_absolute = 0.0;
            max_relative = 0.0;
            $display("--- %0s: %0d vectors ---", name, count);

            for (i = 0; i < count; i = i + 1) begin
                input_a = data_a[i];
                input_b = data_b[i];
                input_c = data_c[i];
                #(`CYCLE);
                check_result(i);
            end

            $display("%0s: checked=%0d passed=%0d failed=%0d", name, checked, checked-errors, errors);
            $fdisplay(report, "SUMMARY %0s checked=%0d passed=%0d failed=%0d",
                      name, checked, checked-errors, errors);
            $fdisplay(report, "Finite comparisons only: max_absolute=%e max_relative=%e",
                      max_absolute, max_relative);
            $fclose(file_ans);
            $fclose(report);
            if (name == "normal") begin
                normal_checked = checked;
                normal_errors = errors;
            end else begin
                special_checked = checked;
                special_errors = errors;
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
        special_checked = 0;
        special_errors = 0;
        // +NORMAL_ONLY skips the special suite; exact bit comparison still applies.
        normal_only = $test$plusargs("NORMAL_ONLY");
        expected_checked = normal_only ? 200 : 215;

        run_dataset("normal", 200, `FILE_A, `FILE_B, `FILE_C, `FILE_GOLDEN,
                    `FILE_ANS, "report_normal.txt");
        if (!normal_only)
            run_dataset("special", 15, `SPECIAL_FILE_A, `SPECIAL_FILE_B,
                    `SPECIAL_FILE_C, `SPECIAL_FILE_GOLDEN,
                    `SPECIAL_FILE_ANS, "report_special.txt");

        // Write a machine-readable completion record only after all comparisons.
        result_file = $fopen("tb_result.json", "w");
        if (!result_file) begin
            $display("ERROR: Cannot write tb_result.json");
            $finish;
        end
        if (total_errors == 0 && total_checked == expected_checked)
            $fdisplay(result_file, "{\"status\":\"passed\",\"checked\":%0d,\"errors\":%0d,\"normal\":{\"checked\":%0d,\"errors\":%0d},\"special\":{\"checked\":%0d,\"errors\":%0d}}",
                      total_checked, total_errors, normal_checked, normal_errors, special_checked, special_errors);
        else
            $fdisplay(result_file, "{\"status\":\"failed\",\"checked\":%0d,\"errors\":%0d,\"normal\":{\"checked\":%0d,\"errors\":%0d},\"special\":{\"checked\":%0d,\"errors\":%0d}}",
                      total_checked, total_errors, normal_checked, normal_errors, special_checked, special_errors);
        $fclose(result_file);

        if (total_errors == 0 && total_checked == expected_checked)
            $display("OVERALL PASS: all %0d vectors correct", total_checked);
        else
            $display("OVERALL FAIL: %0d errors in %0d vectors", total_errors, total_checked);
        $finish;
    end

endmodule

