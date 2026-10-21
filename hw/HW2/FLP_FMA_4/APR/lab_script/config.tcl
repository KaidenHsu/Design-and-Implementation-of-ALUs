
#set_db net:io_rte .skip_routing ture
# Optionally skip routing for the specified net (currently disabled).
#set_db place_detail_legalization_inst_gap 2
# Optionally set the minimum legalization gap between instances (currently disabled).

# Remove existing net/instance assignment constraints.
delete_assigns
# Prevent the initialization flow from creating additional assignments.
set_db init_no_new_assigns 1

# Specify the process technology node in nanometers.
set_db design_process_node 16
# Restrict signal routing to layers M2 through M9.
set_db design_top_routing_layer M9
set_db design_bottom_routing_layer M2

# Recreate the default timing path groups from scratch.
create_basic_path_groups -reset
# Create expanded basic path groups for more detailed timing analysis.
create_basic_path_groups -expanded

# Enable on-chip variation timing analysis.
set_db timing_analysis_type ocv
# Enable common-path pessimism removal for both setup and hold analysis.
set_db timing_analysis_cppr both
# Apply early/late derates for maximum-delay analysis.
set_timing_derate -max -early 0.8 -late 1.0
# Apply early/late derates for minimum-delay analysis.
set_timing_derate -min -early 1.0 -late 1.1

# Limit the fanout of each automatically inserted tie-high/tie-low cell.
set_db add_tieoffs_max_fanout 10
# Limit the maximum distance from a tie-off cell to the cells it drives.
set_db add_tieoffs_max_distance 100
# Specify the standard cells used to tie signals to logic 1 or logic 0.
set_db add_tieoffs_cells {TIEHBWP20P90 TIELBWP20P90}

# Preserve the order specified below when inserting filler cells.
set_db add_fillers_preserve_user_order true
# Specify filler and decoupling cells for physical finishing.
set_db add_fillers_cells {FILL64BWP16P90 DCAP32BWP16P90 DCAP16BWP16P90 DCAP8BWP16P90 DCAP4BWP16P90 FILL2BWP16P90 FILL2BWP16P90LVT FILL1BWP16P90 FILL1BWP16P90LVT}

# Optionally enable automatic antenna diode insertion during routing (disabled).
#set_db route_design_antenna_diode_insertion true
# Optional cell name for the antenna diode to insert (disabled).
#set_db route_design_antenna_cell_name {}

# Load switching-activity information used for power analysis.
source -quiet lab_script/set_activity.tcl
# Disable writing the power database.
set_db power_write_db false
# Disable writing static-current data for power analysis.
set_db power_write_static_currents false

# Enable the early-clock implementation flow.
set_db design_early_clock_flow  true