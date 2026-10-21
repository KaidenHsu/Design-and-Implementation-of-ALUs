# Metal layers and the lab power network

Scope: the TA's 2026-10-07 physical-design deck and all ten scripts in `FLP_FMA_4/APR/lab_script/`, inspected statically. Slide numbers are the printed bottom-right numbers. This describes intended construction and script settings, not a verified routed database.

Deck: ALU_HW2_TA_ADFP_Cell-Based_IC_Physical_Design.pdf`

## Layer map

| Layer | Power structure specified in deck | Direction / location | Slides / script evidence |
|---|---|---|---|
| M11 | No power-ring or stripe assignment in the deck recipe | Mentioned in helper range assumptions and clock rule tables | pns.tcl and config_cts.tcl; not evidence of an M11 power grid. |
| M10 | Upper stacked-via limit for M9 stripes; no power-stripe/ring pattern specified | A limit is not proof of actual metal or via creation | 23-24. |
| M9 | Paired VDD/VSS power stripes | Vertical | 22-24. |
| M8 | Power ring and power stripes | Ring top/bottom sides; horizontal stripes | 17, 24. |
| M7 | Power ring and power stripes | Ring left/right sides; vertical stripes | 17, 24. |
| M6 | Power ring and power stripes | Ring top/bottom sides; horizontal stripes | 20, 24. |
| M5 | Power ring and power stripes | Ring left/right sides; vertical stripes | 20, 24. |
| M4 | Intermediate metal for via connections; no regular power-stripe pattern specified | Local connection geometry depends on generated vias | M5 stripe bottom-via limit M4 on 24; shrink_via operates through M4. |
| M3 | Intermediate metal for via connections; no regular power-stripe pattern specified | Local connection geometry depends on generated vias | VIA23 and VIA34 in shrink_via; M2-M3 addition in post_via_drop. |
| M2 | Additional VDD/VSS power stripes | Horizontal | 30-31; post_via_drop selects M2 `manual_rail` stripes. |
| M1 | Follow-pin power rails along standard-cell rows | Horizontal | Special Route 26-27; M1 legend/rail display on 33; VIA12 handling in shrink_via and post_via_drop. |

All listed power structures carry VDD and VSS on separate conductors. A layer may contain power, clock, and signal geometry; the table does not reserve an entire layer for power.

## Power Rings: slides 17-20

| Ring recipe | Top/bottom metal | Left/right metal | Width | Spacing | Wire-group bits |
|---|---|---|---:|---:|---:|
| Slide 17 | M8 | M7 | 2 | 1.1 | 12 |
| Slide 20 | M6 | M5 | 2 | 1.1 | 12 |

Both recipes enable interleaving, illustrated on slide 18. Slide 20 separately trims excess route portions using `edit_trim_routes -nets {VDD VSS} -layer {M8 M7 M6 M5}`.

`pns.tcl::createPowerRing` implements the corresponding ring concepts with `-type core_rings -follow core`, uniform width/spacing/offset on the four sides, wire grouping, and interleaving. It expands the ring-layer interval by one layer for stacked-via limits:

- M7/M8 ring -> requested via limits M6-M9.
- M5/M6 ring -> requested via limits M4-M7.

These extra via-limit layers do not become additional ring layers. The deck's GUI operations and helper defaults should not be assumed identical in every detail.

## Upper power stripes: M5 ~ M9

The numeric dimensions below are transcribed from the deck; use the loaded technology's distance units (normally micrometers in this APR context).

| Nets | Layer | Direction | Width | Space within set | Set pitch | Start | Top via limit | Bottom via limit | Snap |
|---|---|---|---:|---:|---:|---:|---|---|---|
| VDD VSS | M9 | V | 1.8 | 1.8 | 7.2 | 1.8 | M10 | M8 | Not specified in the table cell |
| VDD | M8 | H | 0.864 | 0 | 2.88 | 0 | M9 | M7 | Half_Grid |
| VSS | M8 | H | 0.864 | 0 | 2.88 | 1.44 | M9 | M7 | Half_Grid |
| VDD | M7 | V | 0.24 | 0 | 7.2 | 7.2 | M8 | M6 | Grid |
| VSS | M7 | V | 0.24 | 0 | 7.2 | 3.6 | M8 | M6 | Grid |
| VDD | M6 | H | 0.24 | 0 | 7.2 | 0 | M7 | M5 | Grid |
| VSS | M6 | H | 0.24 | 0 | 7.2 | 3.6 | M7 | M5 | Grid |
| VDD | M5 | V | 0.24 | 0 | 7.2 | 0 | M6 | M4 | Grid |
| VSS | M5 | V | 0.24 | 0 | 7.2 | 3.6 | M6 | M4 | Grid |

Slides 22-23 explain the Basic/Advanced/Mode dialogs. Relevant settings include extending to the closest ring and connecting to orthogonal targets. The deck notes that a small core may show no long stripe; it suggests utilization 0.4 if the student wants to see stripes after repeating the operation.

### Space, set pitch, start, and snapping

- A **set** is one repeated group: `{VDD VSS}` creates a two-stripe set; `{VDD}` creates a one-stripe set.
- **Space** is the edge-to-edge gap inside a set. `pns.tcl` explicitly sets `add_stripes_spacing_type edge_to_edge`.
- **Set-to-set distance** is the repeated group's pitch, measured between corresponding points in consecutive sets. The helper's argument `pitch` is passed to `-set_to_set_distance`.
- For M9, set width = 1.8 + 1.8 + 1.8 = 5.4. With pitch 7.2, the gap between the last stripe of one set and the first of the next is 1.8.
- For single-net rows, space 0 does not mean touching wires: there is no internal stripe pair. Each net repeats at its own pitch, and a separate start offset interleaves the other net.
- **Start** is the requested offset from the selected starting boundary. Horizontal stripe patterns step vertically; vertical patterns step horizontally. Exact placement also depends on starting-side selection, snapping, and tool behavior.
- M6 VDD requests starts at 0, 7.2, 14.4, ...; VSS at 3.6, 10.8, 18, .... M7 VDD starts one full pitch inward at 7.2 rather than 0.
- **Snap to routing grid**, not ground, constrains stripe center placement: Grid aligns to tracks; Half_Grid permits half-track increments; None disables that grid snapping. This is unrelated to selecting VSS.

## Follow-pin rails and later M2 stripes

### Slides 26-29: follow-pin routing and via adjustment

Slide 26 uses Special Route with nets VDD/VSS, only Follow pins selected, jogging disabled, via connections to Core Ring and Stripe enabled, and crossover top stack layer M5. Slide 27 shows dialog settings.

Cell VDD/VSS **pins** are shapes defined inside library cells. A **follow-pin rail** is routing along aligned power pins in a standard-cell row. The GUI label "Follow pins" names this routing operation; `followpin` is a shape classification used by the scripts. It is not an extra kind of transistor pin.

Slide 28 sources `shrink_via.tcl`: generated VIA12/VIA23 matching follow-pin routing are updated with height/width scales 70/130; VIA34 with 100/130, within M1-M4 limits. This updates selected existing vias, rather than creating an M3/M4 stripe mesh.

Slide 29 is a top-down conceptual view. Blue bars show follow-pin rails; red internal bars illustrate existing standard-cell metal; the VIA-marked metal footprint illustrates how a new connection can encounter a forbidden-spacing condition relative to cell metal. A via includes inter-layer contacts (cuts) and surrounding metal enclosure. The slide does not identify every red shape's net or provide the full numerical rule. Reshaping vias is related to this issue, but DRC is needed to establish the result.

### Slides 30-31: additional horizontal M2 stripes

| Net | Layer | Direction | Width | Space | Set pitch | Start | Via limits | Snap |
|---|---|---|---:|---:|---:|---:|---|---|
| VSS | M2 | H | 0.064 | 0 | 1.152 | 0.544 | M1-M3 | None |
| VDD | M2 | H | 0.064 | 0 | 1.152 | -0.032 | M1-M3 | None |

Extend to closest target Ring; the slides say to unselect pad/core and block-ring connection controls. These M2 stripes are added **after** the initial follow-pin operation, whereas the M5-M9 mesh was created earlier.

Slide 32 checks special-net connectivity. Slide 33 checks DRC and shows zero violations in the example. Its legend identifies blue M1 and red M2; those colors identify layers, not VDD versus VSS. A successful DRC check is distinct from a successful connectivity check.

## Vertical connectivity: what is known and what must be checked

Illustrative adjacent-layer chain, not proof of one continuous stack at every crossing:

```text
M9 vertical stripe
  | VIA89
M8 horizontal stripe / ring
  | VIA78
M7 vertical stripe / ring
  | VIA67
M6 horizontal stripe / ring
  | VIA56
M5 vertical stripe / ring
  | VIA45
M4 local connecting metal
  | VIA34
M3 local connecting metal
  | VIA23
M2 horizontal stripe
  | VIA12
M1 follow-pin rail -> cell power pins
```

A connection may travel sideways on metal between via locations; all cuts need not form a single aligned tower. Each via connects adjacent layers, so M4-to-M2 requires M4-M3 and M3-M2 connections with intervening metal. There is no regular M4 power-stripe recipe in this deck.

Top/bottom stacked-via limits permit generation in a layer range; they neither force every layer to receive a stripe nor guarantee every crossing is connected. VDD must connect to VDD and VSS to VSS. Overlap in a top-down display is not sufficient evidence of electrical connection.

### Later scripts: additional vias and cleanup

- **post_via_drop.tcl, slide 53:** select M2 stripes with `sub_class manual_rail`; add M1-M2 vias tagged `VIA12_Manual` and M2-M3 vias tagged `VIA23_Manual`. The former permits non-orthogonal targets; the latter requires orthogonal targets. Both use exact crossover sizing and explicit exclusion lists.
- **Selection caveat:** `createPowerStripeRail` assigns `user_class manual_rail`; the post script selects `sub_class manual_rail`. Verify the actual object properties and nonempty selection in Innovus. The slides' GUI recipe does not explicitly document the label.
- **fix_manual_via12.tcl, slides 53-54:** read existing Cut_Spacing markers and remove `VIA12_Manual` special vias in their bounding boxes. It does not remove VIA23_Manual or run DRC itself; its check commands are commented out.
- **Slide 55:** final DRC and regular-net connectivity checks follow the post-processing steps. After any via deletion, confirm required power connectivity as well; deleting a DRC offender does not by itself prove the remaining network is connected.

## Relevant information from the remaining scripts

| Script | Layer / power relevance |
|---|---|
| config.tcl | General route range M2-M9 does not remove M1 cell rails or establish special-net topology. Tie cells and filler/decap lists configure later physical-cell insertion. Active source of missing set_activity.tcl is a runtime dependency to resolve. |
| config_cts.tcl | Clock trunks prefer M6-M7 with VSS shielding; leaves prefer M4-M5. Width/spacing rule entries reach M11. These are clock rules, not additional power-stripe assignments. |
| pns.tcl | Geometry is created only when helpers are called. Regular stripe helper uses adjacent via limits; rail helper hardcodes M1-M5 and assigns manual_rail. Ring limits expand beyond ring layers. |
| add_well_taps.tcl | Inserts TAPCELLBWP20P90LVT with checkerboard placement and rule 50.76 on slide 35; adds physical cells rather than stripe geometry. |
| write_netlist.tcl | Exports connectivity, including a PG/physical-cell version. Does not define new power layers. |
| write_sdf.tcl | Exports timing data for two analysis views; does not create metal or vias. |
| write_stream.tcl | Exports implemented geometry with the technology GDS layer map and merged library GDS; does not construct the network. |

See [script_slide_guide.md](script_slide_guide.md) for the complete script-to-slide mapping and per-script details.
