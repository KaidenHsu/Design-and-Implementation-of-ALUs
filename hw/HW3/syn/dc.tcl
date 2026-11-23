# Adapted from the user's HW1 dc.tcl; one top/configuration per DC process.
# $ TOP=DP4_pipe SYN_CONFIG=delay PERIOD_NS=20 dcnxt_shell -f HW3/syn/dc.tcl

# Resolve the authoritative project scripts independently of the tool working directory.
set sh_command_log_file /dev/null
set script_dir [file dirname [file normalize [info script]]]
set project_root [file dirname $script_dir]
source [file join $script_dir .synopsys_dc.setup]
set Company NSYSU2026ALU
set Designer {Kaiden Hsu}

# Launcher settings must be explicit for reproducible manual and automated runs.
proc required_env {name} {
    if {![info exists ::env($name)] || $::env($name) eq ""} {
        error "Required environment variable missing: $name"
    }
    return $::env($name)
}

# DC report units are converted once; all launcher periods are in ns.
proc time_unit_ns {text} {
    set pattern {^\s*Time(?:_|\s+)units?\s*:\s*([0-9.eE+-]+)\s*(Seconds?|[munpf]?s)(?:\([^)]*\))?\s*$}
    if {![regexp -nocase -line $pattern $text all number suffix]} {
        error "Cannot determine library time unit; inspect units report"
    }
    set suffix [string tolower $suffix]
    if {$suffix in {second seconds}} {set suffix s}
    set scales [dict create s 1e9 ms 1e6 us 1e3 ns 1 ps 1e-3 fs 1e-6]
    set result [expr {double($number)*[dict get $scales $suffix]}]
    if {$result <= 0} {error "Library time unit must be positive"}
    return $result
}

proc validate_output_path {project_root config Top out} {
    # Only direct, numbered trials of this design/strategy may receive DC files.
    set root [file normalize [file join $project_root syn $config $Top tune]]
    set output [file normalize $out]
    set name [file tail $output]
    if {[file dirname $output] ne $root ||
        ![regexp {^([0-9]{3,})_period_([0-9]+(?:\.[0-9]+)?)ns$} $name all index period]} {
        error "HW3_OUT must be a numbered trial under syn/$config/$Top/tune"
    }
    scan $index %d sequence
    if {$sequence <= 0 || $period <= 0} {error "Invalid trial index or period"}
    return $output
}

# One DC process handles one top/strategy/period; Python schedules independent runs.
proc main {} {
    global project_root script_dir library_root target_library
    # 1. Select the top, strategy, and target clock period.
    set Top [required_env TOP]
    set config [required_env SYN_CONFIG]
    set period_ns [required_env PERIOD_NS]
    if {$Top ni {DP4 DP4_pipe DP4_CG}} {error "Invalid TOP: $Top"}
    if {$config ni {area mid delay}} {error "SYN_CONFIG must be area, mid, or delay"}
    if {![string is double -strict $period_ns] || $period_ns <= 0 ||
        [string match -nocase *inf* $period_ns]} {
        error "PERIOD_NS must be finite and positive"
    }



    # 2. Check the HW1 technology libraries and implemented top source.
    foreach db $target_library {
        if {![file isfile [file join $library_root $db]]} {error "Missing library: $db in $library_root"}
    }
    set source [file join $project_root rtl ${Top}.v]
    if {![file isfile $source]} {error "Missing RTL: $source"}
    # Skeletons are intentionally unfinished; do not produce misleading results.
    set fd [open $source r]; set rtl [read $fd]; close $fd
    if {[string first {Output intentionally undriven} $rtl] >= 0} {
        error "RTL skeleton is not implemented: $source"
    }



    # 3. Publish by design for manual runs, or retain the launcher's numbered trial.
    set out [file join $script_dir $config $Top]
    if {[info exists ::env(HW3_OUT)]} {
        set out [validate_output_path $project_root $config $Top $::env(HW3_OUT)]
    }
    file mkdir $out
    define_design_lib WORK -path [file join $out ${Top}_work]

    # 4. Analyze an ordered Verilog source list, then elaborate and link the top.
    set sources [list $source]
    if {[info exists ::env(HW3_RTL_LIST)]} {
        set fd [open $::env(HW3_RTL_LIST) r]
        set sources [split [string trim [read $fd]] \n]
        close $fd
    }
    foreach source $sources {
        if {![file isfile $source]} {error "Missing RTL: $source"}
        set ::search_path [concat [list [file dirname $source]] $::search_path]
        if {![analyze -format verilog -library WORK $source]} {error "analyze failed: $source"}
    }
    if {[elaborate $Top -library WORK] eq "0"} {error "elaborate failed: $Top"}
    current_design $Top
    if {![link]} {error "link failed"}
    if {![check_design]} {error "check_design failed"}



    # 5. Read native time units before applying timing constraints.
    redirect -variable units {report_units}
    set fd [open [file join $out ${Top}_units_report.txt] w]
    puts $fd $units; close $fd
    set period [expr {double($period_ns)/[time_unit_ns $units]}]

    # DP4 uses a virtual clock; pipelines use the physical clk port.
    if {$Top eq "DP4"} {
        create_clock -name clk -period $period
    } else {
        set clock_port [get_ports clk]
        create_clock -name clk -period $period $clock_port
        set_fix_hold [get_clocks clk]
        set_dont_touch_network [get_clocks clk]
        set_ideal_network $clock_port
    }

    set data_inputs [remove_from_collection [all_inputs] [get_ports {clk rst}]]
    # Match the testbench's input launch timing before rising-edge capture.
    if {$Top eq "DP4"} {
        set_input_delay 0 -clock clk $data_inputs
    } else {
        set_input_delay 0 -clock clk -clock_fall $data_inputs
    }

    # DP4's virtual clock leaves half a period for input-to-output computation.
    # Do not also apply set_max_delay: output delay would consume that budget.
    # Pipelines have half a period for input-to-S1 and S4-to-output paths,
    # and a full period between rising-edge pipeline registers.
    set_output_delay [expr {$period/2.0}] -clock clk [all_outputs]

    # Reset is inactive in functional timing analysis; reset is tested in TB.
    set_case_analysis 0 [get_ports rst]



    # 6. Preserve HW1 min/max corners and the ZeroWireload model.
    set_operating_conditions \
        -min_library N16ADFP_StdCellff0p88v125c_ccs -min ff0p88v125c \
        -max_library N16ADFP_StdCellss0p72vm40c_ccs -max ss0p72vm40c
    set_wire_load_model -name ZeroWireload -library N16ADFP_StdCellss0p72vm40c_ccs
    set_wire_load_mode top



    # 7. Area adds an area target; mid/delay use their searched timing targets.
    if {$config eq "area"} {set_max_area 0}
    if {$Top eq "DP4_CG"} {
        # Course manual clock-gating flow: convert RTL gates into safe ICG cells.
        if {[replace_clock_gates] eq "0"} {error "clock-gate replacement failed"}
    }
    if {[compile] eq "0"} {error "compile failed"}



    # 8. Produce legal Verilog names without altering the announced top ports.
    set ::bus_inference_style {%s[%d]}
    set ::bus_naming_style {%s[%d]}
    set ::hdlout_internal_busses true
    change_names -hierarchy -rule verilog
    define_name_rules name_rule -allowed "A-Z a-z 0-9 _" -max_length 255 -type cell
    define_name_rules name_rule -allowed "A-Z a-z 0-9 _\[\]" -max_length 255 -type net
    define_name_rules name_rule -map {{"\*cell\*" "cell"}} -case_insensitive
    change_names -hierarchy -rules name_rule



    # 9. Keep timing coverage, area, power, and clock-gating evidence.
    # Preserve the fixed top-level clk/rst interface, including unused DP4 ports.
    redirect -file [file join $out ${Top}_check_design.txt] {check_design}
    redirect -file [file join $out ${Top}_check_timing.txt] {check_timing}
    redirect -file [file join $out ${Top}_timing_report.txt] {
        report_timing -path full -delay max -max_paths 10 -significant_digits 6 -sort_by group
    }
    redirect -file [file join $out ${Top}_area_report.txt] {report_area -hier -nosplit}
    redirect -file [file join $out ${Top}_power_report.txt] {report_power -analysis_effort low}
    redirect -file [file join $out ${Top}_report.txt] {
        report_qor
        report_area -hier -nosplit
        report_timing -path full -delay max -significant_digits 6 -sort_by group
        report_power -analysis_effort low
    }
    if {$Top eq "DP4_CG"} {
        redirect -file [file join $out ${Top}_clock_gating_report.txt] {
            report_clock_gating -gating_elements
        }
    }



    # 10. Export the same candidate for gate simulation and manual PrimeTime.
    write -hierarchy -format ddc -output [file join $out ${Top}_syn.ddc]
    write -format verilog -hierarchy -output [file join $out ${Top}_syn.v]
    write_sdf -version 2.1 -context verilog [file join $out ${Top}_syn.sdf]
    write_sdc [file join $out ${Top}_syn.sdc]

    # Export presence and parseable reports are independently checked by Python.
    puts "HW3_DC_COMPLETE: $Top $config period=$period_ns ns"
}

# Convert Tcl exceptions to a failing tool exit, retaining the original traceback.
if {[catch {main} failure options]} {
    puts stderr "HW3_DC_ERROR: $failure"
    if {[dict exists $options -errorinfo]} {puts stderr [dict get $options -errorinfo]}
    exit 1
}
exit 0
