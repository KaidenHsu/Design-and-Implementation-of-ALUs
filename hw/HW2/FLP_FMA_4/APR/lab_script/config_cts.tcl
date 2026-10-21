# Define the clock-tree buffers and inverters available to CTS.
set_db cts_buffer_cells {CKBD16BWP16P90LVT CKBD12BWP16P90LVT CKBD8BWP16P90LVT CKBD4BWP16P90LVT CKBD2BWP16P90LVT}
set_db cts_inverter_cells {CKND16BWP16P90LVT CKND12BWP16P90LVT CKND8BWP16P90LVT CKND4BWP16P90LVT CKND2BWP16P90LVT}

# Clock-gating cells are not configured for this CTS run.
#set_db cts_clock_gating_cells{}

# Create separate non-default routing rules for clock trunks and leaves.
create_route_rule -name TrunkNDR \
    -width {M1:M3 0.032 M4:M7 0.04 M8:M9 0.124 M10:M11 0.45}\
    -spacing {M1 0.03 M2:M3 0.032 M4:M7 0.04 M8:M9 0.128 M10:M11 0.45}\
    -generate_via
create_route_rule -name LeafNDR \
    -width {M1:M3 0.032 M4:M7 0.04 M8:M9 0.062 M10:M11 0.45}\
    -spacing {M1 0.03 M2:M3 0.032 M4:M7 0.08 M8:M9 0.064 M10:M11 0.45}\
    -generate_via

# Define the trunk route type only if it does not already exist.
if {[llength [get_db route_types ClkTrunkNDR]] == 0} {
    create_route_type -name ClkTrunkNDR \
                      -route_rule TrunkNDR \
                      -top_preferred_layer 7 \
                      -bottom_preferred_layer 6 \
                      -min_stack_layer 5 \
                      -shield_net VSS
}

# Define the leaf route type only if it does not already exist.
if {[llength [get_db route_types ClkLeafNDR]] == 0} {
        create_route_type   -name ClkLeafNDR \
                            -route_rule LeafNDR \
                            -top_preferred_layer 5 \
                            -bottom_preferred_layer 4 \
                            -min_stack_layer 5 \
}

# Apply the route types to the trunk and leaf portions of the clock tree.
set_db cts_route_type_top ClkTrunkNDR
set_db cts_route_type_trunk ClkTrunkNDR
set_db cts_route_type_leaf ClkLeafNDR

# Set the maximum transition targets for each clock-tree level.
set_db cts_target_max_transition_time_top 150ps
set_db cts_target_max_transition_time_trunk 150ps
set_db cts_target_max_transition_time_leaf 100ps

# Keep generated-clock timing independent of source latency updates.
set_db timing_enable_generated_clock_edge_based_source_latency false
set_db cts_update_clock_latency false