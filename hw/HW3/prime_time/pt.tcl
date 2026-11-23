# Analyze all three designs by default; explicit TOP selects one design.
set project_root [file normalize [file join [file dirname [info script]] ..]]

proc env_or {name fallback} {
    if {[info exists ::env($name)]} {return $::env($name)}
    return $fallback
}

# Select inputs and reject invalid settings before opening any report.
set tops {DP4 DP4_pipe DP4_CG}
if {[info exists ::env(TOP)]} {set tops [list $::env(TOP)]}
set config [env_or SYN_CONFIG delay]
foreach Top $tops {
    if {$Top ni {DP4 DP4_pipe DP4_CG}} {error "TOP must be DP4, DP4_pipe, or DP4_CG"}
}
if {$config ni {area mid delay}} {error "SYN_CONFIG must be area, mid, or delay"}

set lib_dir [env_or CELL_LIB_DIR /cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/CCS]
set search_path [concat [list $lib_dir] $search_path]
set target_library [list N16ADFP_StdCellss0p72vm40c_ccs.db N16ADFP_StdCellff0p88v125c_ccs.db]
set link_library [concat [list *] $target_library]
set sh_source_uses_search_path true

# Preflight every requested design before starting power analysis.
set inputs [dict create]
foreach Top $tops {
    set syn_dir [file join $project_root syn $config $Top]
    set netlist [file join $syn_dir ${Top}_syn.v]
    set sdc [file join $syn_dir ${Top}_syn.sdc]
    set sdf [file join $syn_dir ${Top}_syn.sdf]
    set vcd [file join $project_root gate-level-sim $config $Top wave.vcd]
    foreach input_file [list $netlist $sdc $sdf $vcd] {
        if {![file isfile $input_file]} {
            error "Required input missing: $input_file"
        }
    }
    dict set inputs $Top [list $netlist $sdc $sdf $vcd]
}

set power_enable_analysis true
set power_analysis_mode averaged
set power_report_leakage_breakdowns true
set report_names [dict create DP4 report_power_DP4.rpt DP4_pipe report_power_pipe.rpt DP4_CG report_power_CG.rpt]

# Keep each design's constraints and activity in an independent loaded design.
foreach Top $tops {
    remove_design -all
    lassign [dict get $inputs $Top] netlist sdc sdf vcd
    read_verilog $netlist
    current_design $Top
    link

    read_sdc $sdc
    read_sdf $sdf
    check_timing
    update_timing

    read_vcd -strip_path testbench/dut $vcd
    check_power
    update_power

    # Reports are replaced independently and identify their exact source files.
    set report_file [file join $project_root prime_time [dict get $report_names $Top]]
    set fd [open $report_file w]
    puts $fd "TOP=$Top SYN_CONFIG=$config"
    puts $fd "NETLIST=$netlist\nSDC=$sdc\nSDF=$sdf\nVCD=$vcd"
    close $fd
    report_units >> $report_file
    report_power -hierarchy >> $report_file
}
exit
