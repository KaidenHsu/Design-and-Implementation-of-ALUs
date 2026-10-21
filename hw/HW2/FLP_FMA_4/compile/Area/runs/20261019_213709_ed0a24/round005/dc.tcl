# Adapted from ALU_HW1_TA_dc.tcl. The launcher sets paths and period per attempt.
# Keep TA libraries/corners, ZeroWireload, set_max_area 0, compile, naming,
# report commands, and DDC/Verilog/SDF 2.1/SDC exports.

proc env_or {name fallback} {
    if {[info exists ::env($name)]} { return $::env($name) }
    return $fallback
}

proc checked {command} {
    if {[uplevel 1 $command] eq "0"} { error "DC command failed: $command" }
}

proc main {} {
    set Company "NSYSU2026ALU"
    set Designer "Kaiden Hsu"

    # FLP_FMA_4 is replaced when this template is installed under compile/.
    set Design [env_or HW2_DESIGN "FLP_FMA_4"]
    set tops [dict create FXP_FMA FXP_FMA FLP_FMA FMA FLP_FMA_4 FMA_pipe]
    set Top [dict get $tops $Design]
    set base [file normalize [file join [file dirname [info script]] ..]]
    set Design_root [env_or HW2_DESIGN_ROOT $base]
    set Path_Top [file join $Design_root rtl]
    set Path_Syn [env_or HW2_OUT [file join $Design_root gate_level Between]]
    set Dump_file_name $Top
    set Clk_pin "clk"
    set Period_ns [env_or HW2_PERIOD "0.49"]
    if {![string is double -strict $Period_ns] || $Period_ns <= 0} {
        error "Clock period must be positive"
    }
    file mkdir $Path_Syn work
    define_design_lib WORK -path [file normalize work]

    # Original TA library and tool settings.
    set library_root [env_or HW2_LIBRARY_ROOT /cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/CCS]
    set ::search_path [concat [list $library_root $Path_Top] $::search_path]
    set ::target_library "N16ADFP_StdCellss0p72vm40c_ccs.db N16ADFP_StdCellff0p88v125c_ccs.db"
    set ::link_library "* $::target_library dw_foundation.sldb"
    set ::symbol_library "tsmc040.sdb generic.sdb"
    set ::synthetic_library "dw_foundation.sldb"
    set ::hdlin_translate_off_skip_text "TRUE"
    set ::edifout_netlist_only "TRUE"
    set ::verilogout_no_tri true
    set ::hdlin_enable_presto_for_vhdl "TRUE"
    set ::sh_enable_line_editing true
    set ::sh_line_editing_mode emacs
    history keep 100
    alias h history

    # The runner writes an ordered RTL list. Manual runs can discover rtl/*.v.
    if {[info exists ::env(HW2_RTL_LIST)]} {
        set fd [open $::env(HW2_RTL_LIST) r]
        set sources [split [string trim [read $fd]] \n]
        close $fd
    } else {
        set sources [glob -nocomplain [file join $Path_Top *.v]]
    }
    if {![llength $sources]} { error "No RTL files in $Path_Top" }
    foreach source $sources {
        set ::search_path [concat [list [file dirname $source]] $::search_path]
        checked [list analyze -format verilog $source]
    }
    checked [list elaborate $Top]
    current_design $Top
    checked {link}
    checked {check_design}

    # Period settings from Python are ns; convert to this library's time unit.
    redirect -variable units {report_units}
    set fd [open [file join $Path_Syn units_report.txt] w]
    puts $fd $units
    close $fd
    if {![regexp -nocase -line {Time(?:_|\s+)units?\s*:\s*([0-9.eE+-]+)\s*([A-Za-z]+)} $units all value unit]} {
        error "Cannot determine library time unit"
    }
    set scales [dict create s 1e9 second 1e9 seconds 1e9 ms 1e6 us 1e3 ns 1 ps 1e-3 fs 1e-6]
    set time_ns [expr {$value * [dict get $scales [string tolower $unit]]}]
    set Clk_period [expr {$Period_ns / $time_ns}]

    # Use the TA's virtual-clock alternative for the combinational targets.
    # Preserve the physical-clock settings for FMA_pipe.
    if {$Design eq "FLP_FMA_4"} {
        create_clock -name $Clk_pin -period $Clk_period [get_ports $Clk_pin]
        set_fix_hold [get_clocks $Clk_pin]
        set_dont_touch_network [get_clocks $Clk_pin]
        set_ideal_network [get_ports $Clk_pin]
        set_input_delay 0 -clock $Clk_pin [remove_from_collection [all_inputs] [get_ports $Clk_pin]]
    } else {
        set_max_delay $Clk_period -from [all_inputs] -to [all_outputs]
        create_clock -name $Clk_pin -period $Clk_period
        set_input_delay 0 -clock $Clk_pin [all_inputs]
    }
    set_output_delay 0 -clock $Clk_pin [all_outputs]

    set_operating_conditions -min_library N16ADFP_StdCellff0p88v125c_ccs -min ff0p88v125c \
                            -max_library N16ADFP_StdCellss0p72vm40c_ccs -max ss0p72vm40c
    set_wire_load_model -name ZeroWireload -library N16ADFP_StdCellss0p72vm40c_ccs
    set_wire_load_mode top
    set_max_area 0
    # separate multi-port nets, to ensure final netlist does not contain assign statements
    set_fix_multiple_port_nets -all -buffer_constants [get_designs *]
    checked {compile}

    # wrong naming rules may cause problems in LVS
    set ::bus_inference_style {%s[%d]}
    set ::bus_naming_style {%s[%d]}
    set ::hdlout_internal_busses true
    change_names -hierarchy -rule verilog
    define_name_rules name_rule -allowed "A-Z a-z 0-9 _" -max_length 255 -type cell
    define_name_rules name_rule -allowed "A-Z a-z 0-9 _\[\]" -max_length 255 -type net
    define_name_rules name_rule -map {{"\\*cell\\*" "cell"}}
    define_name_rules name_rule -case_insensitive
    change_names -hierarchy -rules name_rule
    remove_unconnected_ports -blast_buses [get_cells -hierarchical *]

    # Preserve TA report settings; add units/check_timing for result interpretation.
    redirect -file [file join $Path_Syn check_timing.txt] {check_timing}
    report_timing -significant_digits 6 -sort_by group
    redirect -file [file join $Path_Syn timing_report.txt] {
        report_timing -path full -delay max -significant_digits 6 -sort_by group
    }
    redirect -file [file join $Path_Syn area_report.txt] {report_area -hier -nosplit}
    redirect -file [file join $Path_Syn power_report.txt] {report_power -analysis_effort low}

    # Measured stage boundaries for the four registered arithmetic stages.
    # These are from/to paths, not -through paths spanning unrelated stages.
    set stage_file [file join $Design_root compile stage_paths.tcl]
    if {$Design eq "FLP_FMA_4"} {
        if {![file exists $stage_file]} { error "Missing pipeline stage mappings: $stage_file" }
        set stage_paths {}
        source $stage_file
        set stage 0
        foreach mapping $stage_paths {
            incr stage
            lassign $mapping from_pattern to_pattern
            if {$from_pattern eq "INPUTS"} {
                set from [remove_from_collection [all_inputs] [get_ports {clk rst}]]
            } else { set from [get_pins -hierarchical -quiet $from_pattern] }
            if {$to_pattern eq "OUTPUTS"} { set to [all_outputs] } \
            else { set to [get_pins -hierarchical -quiet $to_pattern] }
            if {[sizeof_collection $from] && [sizeof_collection $to]} {
                redirect -file [file join $Path_Syn timing_report_stage${stage}.txt] {
                    report_timing -from $from -to $to -path full -delay max \
                        -nworst 1 -max_paths 1 -significant_digits 6 -sort_by group
                }
            } else { error "Stage $stage has an empty timing boundary: $from_pattern -> $to_pattern" }
        }
        if {$stage != 4} { error "Expected four pipeline stage paths in $stage_file" }
    }

    checked [list write -hierarchy -format ddc -output [file join $Path_Syn ${Dump_file_name}.ddc]]
    checked [list write -format verilog -hierarchy -output [file join $Path_Syn ${Dump_file_name}.v]]
    checked [list write_sdf -version 2.1 -context verilog [file join $Path_Syn ${Dump_file_name}.sdf]]
    checked [list write_sdc [file join $Path_Syn ${Dump_file_name}.sdc]]
    puts "HW2_DC_COMPLETE: $Design period=$Period_ns ns"
}

if {[catch {main} message options]} {
    puts stderr "HW2_DC_ERROR: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
