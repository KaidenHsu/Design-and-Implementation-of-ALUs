set Company 		"VLSI5015"
set Designer 		"default"

#設定40nm製程路徑
set search_path      "/cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/CCS/  $search_path"
#設定40nm製程路徑檔，如果有memory compiler的檔案db檔的路徑，記得在這邊設定
set target_library   "N16ADFP_StdCellss0p72vm40c_ccs.db N16ADFP_StdCellff0p88v125c_ccs.db"
set link_library    "* $target_library dw_foundation.sldb"
set symbol_library "generic.sdb"
set synthetic_library "dw_foundation.sldb"
set hdlin_translate_off_skip_text "TRUE"
set edifout_netlist_only "TRUE"
set verilogout_no_tri true
set hdlin_enable_presto_for_vhdl "TRUE"
set sh_enable_line_editing true
set sh_line_editing_mode emacs
history keep 100
alias h history

#Path_Top:Verilog放置的位置
#Path_Syn:合成後report.txt檔案要放置的根位置，需自行在目錄下創建名為dc_out_file之資料夾
#Dump_file_name:合成後產生檔案之名字
# Resolve design paths relative to this script, not the launch directory.
set Script_dir [file dirname [file normalize [info script]]]
set Design_root [file dirname $Script_dir]
set Path_Top [file join $Design_root rtl]
set Path_Syn [file join $Design_root gate_level SYN]
file mkdir $Path_Syn
# Keep DC intermediate data separate from the comparison synthesis runs.
file mkdir [file join $Script_dir work]
define_design_lib WORK -path [file join $Script_dir work]
set Dump_file_name "FMA_pipe_syn"
#設定Top module 名稱，需跟自行設計之電路的top module name相同
set Top				"FMA_pipe"
#Specify Clock，clock名需和top module中clk port相同
set Clk_pin			"clk"
set Clk_period		"4"
set t_in [expr $Clk_period*0.3]
set t_out 0.02

#Read Design
#如果設計有parameter設計，read_file指定不能用，需使用analyze + elaborate指令並自行更改路徑
# read_file -format verilog {/home/m103040049/HDL_HW/multiplier.v}
# current_design $Top
analyze -format verilog [list [file join $Path_Top FMA_pipe.v]]
elaborate $Top
current_design $Top
link
check_design

#Max Delay (For Combinational Circurt)
#set_max_delay $Clk_period  -from [all_inputs] -to [all_outputs]
#create_clock -name $Clk_pin -period $Clk_period 

#Setting Operating Conditions
set_operating_conditions -min_library N16ADFP_StdCellff0p88v125c_ccs -min ff0p88v125c \
                        -max_library N16ADFP_StdCellss0p72vm40c_ccs -max ss0p72vm40c
#Setting Wire Load Model
set_wire_load_model -name ZeroWireload -library N16ADFP_StdCellss0p72vm40c_ccs                        
set_wire_load_mode top

#Setting Timing Constraints、Specify Clock (For Sequential Circurt)
create_clock -name $Clk_pin -period $Clk_period [get_ports $Clk_pin]
set_dont_touch_network						[get_clocks $Clk_pin]
set_fix_hold									[get_clocks $Clk_pin]
set_clock_uncertainty           0.02    [get_clocks $Clk_pin]
set_clock_latency               0.2     [get_clocks $Clk_pin]
set_input_transition            0.5     [all_inputs]
set_clock_transition            0.1     [all_clocks]
set_ideal_network								[get_ports $Clk_pin]


#Setting Input / Output Delay
set_input_delay    	-min 0.2              -clock $Clk_pin [remove_from_collection [all_inputs] [get_ports $Clk_pin]]
set_input_delay     -max $t_in          -clock $Clk_pin [remove_from_collection [all_inputs] [get_ports $Clk_pin]]
set_output_delay   	$t_out              -clock $Clk_pin [all_outputs]



#02_compile.tcl
set high_fanout_net_threshold 0

uniquify
set_fix_multiple_port_nets -all -buffer_constants [get_designs *]
set compile_seqmap_enable_output_inversion true

#若合成時只考慮面積而不在意時間，則可以不設定Timing Constraints，只設定Max area為0
#set_max_area 0

compile

#Change Naming Rule
set bus_inference_style {%s[%d]}
set bus_naming_style {%s[%d]}
set hdlout_internal_busses true
change_names -hierarchy -rule verilog
define_name_rules name_rule -allowed "A-Z a-z 0-9 _" -max_length 255 -type cell
define_name_rules name_rule -allowed {A-Z a-z 0-9 _[]} -max_length 255 -type net
define_name_rules name_rule -map {{"\\*cell\\*" "cell"}} 
define_name_rules name_rule -case_insensitive
change_names -hierarchy -rules name_rule
remove_unconnected_ports -blast_buses [get_cells -hierarchical *]

#Report
report_timing -sign 4 -path full -delay max -max_paths 1 -nworst 1 > $Path_Syn/timing_setup_report.txt
report_timing -sign 4 -path full -delay min -max_paths 1 -nworst 1 > $Path_Syn/timing_hold_report.txt
report_area -hier -nosplit > $Path_Syn/area_report.txt
report_power -analysis_effort low > $Path_Syn/power_report.txt

#Write out
write -hierarchy -format ddc -output $Path_Syn/${Dump_file_name}.ddc
write -format verilog -hierarchy -output $Path_Syn/${Dump_file_name}.v
write_sdf -version 2.1 -context verilog $Path_Syn/${Dump_file_name}.sdf
write_sdc $Path_Syn/${Dump_file_name}.sdc
# Diagnostics preserve the TA clock period (4 library time units).
redirect -file [file join $Path_Syn units_report.txt] {report_units}
redirect -file [file join $Path_Syn check_timing.txt] {check_timing}

# Report actual register-to-register boundaries after exporting APR inputs.
# Shared with the comparison flow; no s1/s2/s3/s4 module hierarchy is assumed.
set stage_file [file join $Design_root compile stage_paths.tcl]
set stage_paths {}
source $stage_file
if {[llength $stage_paths] != 4} {
    error "Expected four pipeline stage mappings in $stage_file"
}
set stage 0
foreach mapping $stage_paths {
    incr stage
    lassign $mapping from_pattern to_pattern
    if {$from_pattern eq "INPUTS"} {
        set from [remove_from_collection [all_inputs] [get_ports {clk rst}]]
    } else {
        set from [get_pins -hierarchical -quiet $from_pattern]
    }
    set to [get_pins -hierarchical -quiet $to_pattern]
    if {![sizeof_collection $from] || ![sizeof_collection $to]} {
        error "Stage $stage has an empty timing boundary: $from_pattern -> $to_pattern"
    }
    redirect -file [file join $Path_Syn timing_report_stage${stage}.txt] {
        report_timing -from $from -to $to -path full -delay max \
            -nworst 1 -max_paths 1 -significant_digits 6 -sort_by group
    }
}
# Before importing into Innovus, apply physical-design slide 7 to the generated
# gate_level/SYN/FMA_pipe_syn.sdc. A later synthesis run overwrites that file.
