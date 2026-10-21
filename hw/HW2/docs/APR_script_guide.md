# APR scripts and slide guide

Source scripts: `FLP_FMA_4/APR/lab_script/` (all ten files inspected).

Source deck: `C:/Users/ryanh/Documents/Courses/1-3/ALU/Hws/ALU_HW2/ALU_HW2_TA_ADFP_Cell-Based_IC_Physical_Design.pdf`, dated 2026-10-07. Slide numbers refer to the numbers printed at the bottom right. This guide records the deck's workflow; it does not execute its commands. Script behavior is based on static inspection, not an Innovus run.

## Quick map

| Script | Explicit slide reference | Related slides / purpose |
|---|---|---|
| `config.tcl` | 15 | General initialization; settings later affect timing, routing, tie cells, and fillers. |
| `config_cts.tcl` | 15 | Clock-tree configuration used during CTS, 39-43. |
| `pns.tcl` | 17 | Ring concepts 17-20; stripe concepts 22-24 and 30-31. |
| `shrink_via.tcl` | 28 | Follow-pin Special Route 26-27; forbidden-spacing diagram 29. |
| `add_well_taps.tcl` | 35 | Well-tap insertion before standard-cell placement. |
| `post_via_drop.tcl` | 53 | Additional power vias after filler insertion, 52-53. |
| `fix_manual_via12.tcl` | 53-54 | Cleanup using cut-spacing DRC markers. |
| `write_netlist.tcl` | 55-56 | Export post-layout Verilog. |
| `write_sdf.tcl` | 55-56 | Export timing delays for simulation. |
| `write_stream.tcl` | 55-56 | Export physical layout as GDS. |

## 1. config.tcl: initialize the implementation environment

**Slide 15:** `source -quiet lab_script/config.tcl`.

Important active operations:

- `delete_assigns` and `init_no_new_assigns 1`: remove/prevent assignments in the implementation flow. Related background: synthesis netlist and naming precautions on slides 5-6.
- Set the process node to 16 nm and routing limits to **M2-M9**. These routing limits do not mean that power rails cannot exist on M1 or that every layer receives a power stripe.
- Reset/create timing path groups and expanded path groups.
- Enable OCV analysis and common-path pessimism removal for setup and hold. Set maximum-delay derates to early 0.8 / late 1.0, and minimum-delay derates to early 1.0 / late 1.1. Related timing-report stages: slides 36-37, 41-43, and 47-50.
- Set tie-cell maximum fanout 10, maximum distance 100, and cell choices `TIEHBWP20P90` / `TIELBWP20P90`. Related operation: Add Tie HI/LO on slide 44.
- Configure an ordered list of filler/decap cells. It configures later insertion, rather than inserting fillers immediately. Slides 52-53 perform filler insertion.
- Source `lab_script/set_activity.tcl`; disable power database and static-current output; enable early-clock flow.

**Dependency found:** `set_activity.tcl` is absent from the inspected directory. This active `source` can interrupt execution if the file is unavailable at runtime; commands after it may not execute. The commented antenna-diode and legalization options are inactive.

## 2. config_cts.tcl: configure clock-tree implementation

**Slide 15:** source this file during initialization. **Slides 39-43:** the configuration becomes relevant when building and optimizing the clock tree; slide 41 invokes `ccopt_design`.

- Select CKBD buffers and CKND inverters for CTS.
- Define `TrunkNDR` and `LeafNDR` non-default width/spacing rules, with via generation. The rule tables contain entries through M11; defining an entry does not prove that a wire will be routed there.
- Create `ClkTrunkNDR` if absent: preferred layers **M6-M7**, minimum stack layer **M5**, shield net **VSS**.
- Create `ClkLeafNDR` if absent: preferred layers **M4-M5**, minimum stack layer **M5**.
- Assign the trunk route type to top/trunk clock routing and the leaf type to clock leaves.
- Target transitions: top/trunk 150 ps; leaf 100 ps.
- Disable generated-clock edge-based source-latency behavior and CTS clock-latency updates using the two final settings.

These are **clock-routing settings**, not declarations of power-ring or power-stripe layers. Naming VSS as a shield net does not itself create the main power mesh.

## 3. pns.tcl: reusable power-network procedures

**Slide 17:** `source lab_script/pns.tcl`. The deck then creates geometry through GUI dialogs. Sourcing this file registers procedures; it does not execute their bodies or create metal by itself.

| Procedure | Behavior | Slide connection |
|---|---|---|
| `createPowerRing` | Follow the core boundary; choose horizontal/vertical layers; apply width, spacing, offset; enable wire grouping and interleaving. | Direct concept match: 17-20; interleaving diagram: 18. |
| `createPowerStripe` | Create repeated stripes; use adjacent routing layers for stacked-via and ring-connection limits; accept snapping as an argument. | 22-24; same stripe concepts also used in 30-31. |
| `createPowerStripeRail` | Create stripes with explicit pad/core and block-ring connection limits; hardcode stacked-via range M1-M5; assign `user_class manual_rail`. | Related stripe/ring-connection concepts: 23-24 and 30-31; no explicit invocation shown. |
| `createSelectBlockStripe` | Similar to regular stripes, but enable `-over_power_domain 1` and disallow jogs. | General stripe concepts 22-24; this power-domain variant is not explicitly demonstrated. |
| `createRegionStripe` | Restrict stripes to core or die bounding box; use adjacent via limits clamped to indices 1-11; do not extend to the closest ring. | Related core/selected-area concept on 22; not an exact reproduction of its GUI recipe. |

Common argument mapping: `direction` = H/V; `layer` = stripe metal; `nets` = VDD/VSS selection; `offset` = start; `width` = stripe width; `spacing` = gap within a set; `pitch` = set-to-set distance; `snap` = routing-grid snapping.

Implementation details worth retaining:

- All stripe helpers use `-start_from bottom`; do not assume identical starting-side behavior for every direction without checking tool behavior.
- The ring helper expands the lower/higher ring-layer indices by one to set via limits. M7/M8 rings therefore request limits M6-M9; M5/M6 rings request M4-M7. This does not add rings on those extra layers.
- Regular and power-domain stripe helpers leave a neighboring-layer variable unset if called on an endpoint layer (M1 or M11 under their assumptions).
- The ring helper can compute bottom index 0 when the lowest ring layer is index 1; it also computes an unused `botLayer` variable.
- `initializePG` and `initializeRegionBKG` are commented out and are not defined. Their reference code would delete POWER geometry and compute regions outside the core / row area.
- The route trimming on slide 20 is a separate GUI-console step, not part of `createPowerRing`.

## 4. shrink_via.tcl: reshape existing follow-pin vias

**Slide 28:** source after Special Route (26-27). **Slide 29:** related forbidden-spacing illustration.

1. Select follow-pin vias with names matching `VIAGEN12*` and `VIAGEN23*`.
2. Update selected vias within M1-M4 limits using height scale 70 and width scale 130.
3. Deselect all objects.
4. Select `VIAGEN34*` follow-pin vias; update with height scale 100 and width scale 130.

The commands update existing selected vias, rather than declaring a new stripe layer. Despite the filename, some dimensions are enlarged. Exact cut arrays and enclosure results depend on the technology and tool. Slide 29's abbreviated dimensions do not establish the complete numerical forbidden-spacing rule.

## 5. add_well_taps.tcl: insert well-tap cells

**Slide 35:** source immediately before standard-cell placement.

Configure `TAPCELLBWP20P90LVT` with `rule 50.76`, then run `add_well_taps -checker_board`. This inserts physical tap cells; it does not draw power rings or stripes. The exact rule interpretation is tool-specific.

## 6. post_via_drop.tcl: add lower-layer power connections

**Slide 53:** source after adding fillers (52-53).

- Reset special-via rule preference to `default`.
- Select M2 stripe shapes with `sub_class manual_rail`.
- Add **M1-M2** vias on selected wires, tagged `VIA12_Manual`; allow non-orthogonal targets (`orthogonal_only 0`), use exact crossover size, and supply `split_long_via {0.2 0.2 -1 -1}`.
- Add **M2-M3** vias, tagged `VIA23_Manual`; require orthogonal targets (`orthogonal_only 1`) and exact crossover size.
- Both commands exclude listed pin types and wire-shape types from via insertion; their exclusion lists differ slightly.

**Selection dependency:** the matching label is assigned by `createPowerStripeRail` through `-user_class manual_rail`. The post script queries `-sub_class manual_rail`; their runtime relationship should be checked in Innovus. The deck's GUI stripe steps do not explicitly document assigning this label. Confirm the selection is nonempty before assuming vias are added. Neither this script nor the deck establishes that it creates all possible missing connections.

## 7. fix_manual_via12.tcl: remove selected cut-spacing offenders

**Slide 53:** run DRC first, then source the script. **Slide 54:** shows the script.

Read existing design markers whose subtype is `Cut_Spacing`. For each marker bounding box, find special vias in that area and delete only those whose `user_class` is `VIA12_Manual`.

It does not repair every DRC category, delete `VIA23_Manual`, or prove which via caused each marker. It uses the marker's area as a filter. Its own DRC commands are commented out, so it relies on existing markers. Recheck DRC and connectivity afterward; slide 55 performs final checks.

## 8. write_netlist.tcl: export logical and physical netlists

**Slides 55-56.** Remove empty hierarchical instances, then export:

- `outputs/FMA_pipe_pr.v`: post-layout Verilog using the default export options.
- `outputs/FMA_pipe_pg.v`: include power/ground ports and physical cells matching queried TAP, BOUNDARY, DCAP, PVDD, FILL, PFILL, and PCORNER patterns.

The included cell lists depend on matches in the loaded database. This writes connectivity, not detailed polygon geometry.

## 9. write_sdf.tcl: export timing data

**Slides 55-56.** Write `FMA_pipe_ff0p88v125c.sdf` and `FMA_pipe_ss0p72vm40c.sdf` under `outputs/`, using the corresponding `AV_func_*` analysis views, `-map_removal`, and `-recompute_delaycal`.

Each output uses its single named corner for all three max/typical/min view arguments; the triplet is not three different process corners within that file. The analysis views must already exist. No power metal is created.

**Deck path discrepancy:** slide 55 says `lab_APR_script/write_sdf.tcl`; the actual inspected file is in `lab_script/`.

## 10. write_stream.tcl: export GDS geometry

**Slides 55-56.** Configure text size; prepare local `stream_out_map` from the technology mapping file if the local file is absent; append `CUSTOM_CB CUSTOM 108 250` when copying. Export `outputs/FMA_pipe.gds`, merge standard-cell GDS, use library name `DesignLib`, uniquify cell names, pass `-unit 1000`, and write `write_stream.log`.

Technology and library paths point into `/cad/CBDK/ADFP/...`, so this expects the lab environment. Existing local map files are reused. Export preserves implemented layout rather than constructing its power network.

## Suggested reading and execution context

Read in deck order: configuration scripts -> pns helpers -> shrink_via -> add_well_taps -> post_via_drop -> fix_manual_via12 -> netlist/SDF/GDS exports.

These are not a complete automatic flow. The deck includes intervening GUI operations: global-net connections (14), floorplan (16), power construction (17-24), checks (25, 32-33), Special Route (26), M2 stripes (30-31), placement (35), CTS (41), routing (46), fillers (52-53), and final checks (55).

See [metal_layer_power_network.md](metal_layer_power_network.md) for layer assignments, stripe dimensions, via paths, and terminology.
