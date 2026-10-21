`timescale 1ns/1ps

// Combinational signed fixed-point FMA baseline from the HW2 handout.
// Keep the full 64-bit product and add the sign-extended, unshifted C.
// Return the upper 32 bits of the sum; no rounding or saturation is applied.
module FXP_FMA (
    input  wire signed [31:0] a,
    input  wire signed [31:0] b,
    input  wire signed [31:0] c,
    output wire signed [31:0] d
);

    wire signed [63:0] product;
    wire signed [63:0] c_extended;
    wire signed [63:0] sum;

    assign product = a * b;
    assign c_extended = {{32{c[31]}}, c};
    assign sum = product + c_extended;

    assign d = sum[63:32];

endmodule
