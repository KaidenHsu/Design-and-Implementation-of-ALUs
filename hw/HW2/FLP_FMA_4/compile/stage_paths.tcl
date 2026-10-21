# FMA_pipe has four register stages, including registered d and overflow.
# DC names scalar/vector registers <RTL name>_reg or <RTL name>_reg[bit].
# Lists of patterns include arithmetic data and special-result control banks.
# Stage 4 ends at output-register D pins, not the output ports after clock-to-Q.
set stage_paths {
    {INPUTS {*_s1_reg*/D}}
    {{*_s1_reg*/Q*} {*_s2_reg*/D}}
    {{*_s2_reg*/Q*} {*_s3_reg*/D}}
    {{*_s3_reg*/Q*} {d_reg*/D overflow_reg*/D}}
}
