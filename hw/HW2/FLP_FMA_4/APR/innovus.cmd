#######################################################
#                                                     
#  Innovus Command Logging File                     
#  Created on Tue Oct 20 16:09:09 2026                
#                                                     
#######################################################

#@(#)CDS: Innovus v21.17-s075_1 (64bit) 03/15/2023 11:17 (Linux 3.10.0-693.el7.x86_64)
#@(#)CDS: NanoRoute 21.17-s075_1 NR230308-2354/21_17-UB (database version 18.20.604) {superthreading v2.17}
#@(#)CDS: AAE 21.17-s026 (64bit) 03/15/2023 (Linux 3.10.0-693.el7.x86_64)
#@(#)CDS: CTE 21.17-s025_1 () Mar 14 2023 11:00:06 ( )
#@(#)CDS: SYNTECH 21.17-s007_1 () Feb 20 2023 06:56:35 ( )
#@(#)CDS: CPE v21.17-s068
#@(#)CDS: IQuantus/TQuantus 21.1.1-s939 (64bit) Wed Nov 9 09:34:24 PST 2022 (Linux 3.10.0-693.el7.x86_64)

set_db init_power_nets VDD
set_db init_ground_nets VSS
read_mmmc mmmc.view.stylus
#@ Begin verbose source mmmc.view.stylus (pre)
create_library_set -name lib_ss0p72v125c\
    -timing\
    {/cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/CCS/N16ADFP_StdCellss0p72v125c_ccs.lib}
create_library_set -name lib_ss0p72vm40c\
    -timing\
    {/cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/CCS/N16ADFP_StdCellss0p72vm40c_ccs.lib}
create_library_set -name lib_ff0p88v125c\
    -timing\
    {/cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/CCS/N16ADFP_StdCellff0p88v125c_ccs.lib}
create_library_set -name lib_ff0p88vm40c\
    -timing\
    {/cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/CCS/N16ADFP_StdCellff0p88vm40c_ccs.lib}
create_timing_condition -name TC_ss0p72v125c\
    -library_sets lib_ss0p72v125c
create_timing_condition -name TC_ff0p88vm40c\
    -library_sets lib_ff0p88vm40c
create_timing_condition -name TC_ss0p72vm40c\
    -library_sets lib_ss0p72vm40c
create_timing_condition -name TC_ff0p88v125c\
    -library_sets lib_ff0p88v125c
create_rc_corner -name RC_best\
    -pre_route_res 1\
    -post_route_res 1\
    -pre_route_cap 1\
    -post_route_cap 1\
    -post_route_cross_cap 1\
    -pre_route_clock_res 0\
    -pre_route_clock_cap 0\
    -qrc_tech /cad/CBDK/ADFP/Executable_Package/Collaterals/Tech/RC/N16ADFP_QRC/best/qrcTechFile
create_rc_corner -name RC_worst\
    -pre_route_res 1\
    -post_route_res 1\
    -pre_route_cap 1\
    -post_route_cap 1\
    -post_route_cross_cap 1\
    -pre_route_clock_res 0\
    -pre_route_clock_cap 0\
    -qrc_tech /cad/CBDK/ADFP/Executable_Package/Collaterals/Tech/RC/N16ADFP_QRC/worst/qrcTechFile
create_delay_corner -name DC_ff0p88v125c\
    -timing_condition {TC_ff0p88v125c}\
    -rc_corner RC_best
create_delay_corner -name DC_ss0p72v125c\
    -timing_condition {TC_ss0p72v125c}\
    -rc_corner RC_worst
create_delay_corner -name DC_ff0p88vm40c\
    -timing_condition {TC_ff0p88vm40c}\
    -rc_corner RC_best
create_delay_corner -name DC_ss0p72vm40c\
    -timing_condition {TC_ss0p72vm40c}\
    -rc_corner RC_worst
create_constraint_mode -name CM_func\
    -sdc_files ../gate_level/SYN/FMA_pipe_syn.sdc
create_analysis_view -name AV_func_ff0p88v125c -constraint_mode CM_func -delay_corner DC_ff0p88v125c
create_analysis_view -name AV_func_ff0p88vm40c -constraint_mode CM_func -delay_corner DC_ff0p88vm40c
create_analysis_view -name AV_func_ss0p72v125c -constraint_mode CM_func -delay_corner DC_ss0p72v125c
create_analysis_view -name AV_func_ss0p72vm40c -constraint_mode CM_func -delay_corner DC_ss0p72vm40c
set_analysis_view   -setup {AV_func_ss0p72v125c AV_func_ss0p72vm40c} -hold {AV_func_ss0p72v125c AV_func_ss0p72vm40c AV_func_ff0p88v125c AV_func_ff0p88vm40c}\
                    -leakage AV_func_ff0p88v125c -dynamic AV_func_ff0p88v125c
#@ End verbose source mmmc.view.stylus
exit
