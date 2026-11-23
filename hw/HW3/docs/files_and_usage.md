# HW3 Files And Usage

The frontend workflow runs on the course Linux server. It requires Bash, Python
3.8 or newer, `flock`, VCS (`vcs`), and Design Compiler (`dcnxt_shell`) in `PATH`.
Load the course EDA environment before running tools. PrimeTime is a manual step.
The [combinational top](../rtl/DP4.v) and [pipeline top](../rtl/DP4_pipe.v) are implemented. The [clock-gated top](../rtl/DP4_CG.v) is implemented with a retained FP32 operand bank and low-phase latch-based clock gating.

## Files

### Project Layout

```text
HW3/
|-- run.sh
|-- scripts/                       # Scheduler, helpers, and tests
|-- rtl/                           # Standalone design sources
|-- testdata/                      # Shared vectors and reference logs
|-- rtl-sim/
|   |-- TB.v
|   `-- <top>/
|       |-- run.log
|       |-- compile.log
|       |-- sim.log
|       |-- completion.json
|       |-- verification.json
|       |-- wave.vcd
|       `-- work/                  # VCS executable, csrc, native scratch
|-- syn/
|   |-- dc.tcl
|   |-- .synopsys_dc.setup
|   `-- <strategy>/<top>/
|       |-- run.log                # Stage progress and tool output
|       |-- synth.log              # Combined DC trial transcripts
|       |-- search.json            # Ordered trial history
|       |-- selected.json          # Selected trial and export hashes
|       |-- <top>_syn.*            # Published selected exports
|       |-- <top>_*                # Published selected reports
|       `-- tune/
|           |-- 001_period_10ns/
|           |   |-- trial.json
|           |   |-- synth.log
|           |   |-- rtl_files.txt
|           |   |-- <top>_syn.*
|           |   |-- <top>_*
|           |   `-- ...            # DC scratch and WORK library
|           `-- 002_period_8.5ns/   # Same files for the next trial
|-- gate-level-sim/
|   |-- TB.v
|   `-- <strategy>/<top>/
|       |-- run.log
|       |-- compile.log
|       |-- sim.log
|       |-- sdf_annotation.rpt
|       |-- completion.json
|       |-- verification.json
|       |-- wave.vcd
|       `-- work/                  # VCS build and native SDF report
|-- prime_time/                    # Manual power analysis
|-- docs/
`-- result/
    |-- summary.csv                # Current aggregate results
    |-- tasks.json                 # Latest scheduled task states
    `-- run.lock                   # Project-wide invocation lock
```

| Important file | Purpose |
|---|---|
| `run.sh` | Single supported entry point; anchors paths, locks the project, and starts Python. |
| `scripts/workflow.py` | CLI, task dependencies, tuning, simulation, selection, and collection. |
| `scripts/helper.py` | Process execution, transcripts, safe cleanup, hashing, and evidence parsing. |
| `scripts/test_workflow.py` | Tests using temporary projects and fabricated EDA outputs. |
| [Combinational Top](../rtl/DP4.v) | Combinational datapath; arithmetic sections have no clock boundaries. |
| [Pipeline Top](../rtl/DP4_pipe.v) | Four register banks with a final combinational stage. |
| [Local RTL Tests](../scripts/test_rtl.py) | Icarus simulation of actual tops, stress cases, reset, and latency. |
| `rtl/DP4_CG.v` | Five-stage pipeline with clock gating. |
| `testdata/FP16.txt`, `testdata/FP32.txt` | Supplied operands in fixed `x1 x2 x3 x4 y1 y2 y3 y4` order. |
| [Reference Generator](../testdata/generate_vectors.py) | Architectural expected results and a separate exact fused reference. |
| `testdata/test_reference.py` | Known-answer and exact-rational checks of that reference. |
| `testdata/vectors.txt` | Precision, eight 32-bit inputs, and expected output per row. |
| `rtl-sim/TB.v`, `gate-level-sim/TB.v` | Identical, self-contained Verilog-2001 testbenches. |
| `syn/dc.tcl` | Authoritative synthesis script, adapted from HW1. |
| `syn/.synopsys_dc.setup` | Shared library settings, explicitly sourced by `dc.tcl`. |
| `prime_time/pt.tcl` | Project-relative manual VCD-based power analysis. |

The architectural model uses magnitude-truncated Q2.50 alignment in both
precisions, signed Q5.50 accumulation, nearest-even rounding, gradual underflow,
and special-value handling. The exact fused model is retained separately and is
not used as the finite-width RTL's pass/fail oracle. See [RTL Design](rtl_design.md).

The default workload has 2,048 cases, with 1,024 cases per precision. FP16 values
occupy bits `[31:16]`, and the ignored lower halves contain nonzero test patterns.
The generator supports alternating or grouped precision schedules.

See [testbench.md](testbench.md) for the tasks, scoreboard, cycle example, reset,
and comparison rules. The generator and reference checks can also run without EDA:

```sh
$ python3 -B testdata/test_reference.py
$ python3 -B testdata/generate_vectors.py
```

### Command Transcripts

Paths below are relative to `HW3/`. `<top>` is one of the three announced module
names; `<strategy>` is `area`, `mid`, or `delay`.

| Log | Contents |
|---|---|
| `testdata/run.log` | Reference-stage launcher command, progress, Python commands/output, and final status. |
| `rtl-sim/<top>/run.log` | Per-design RTL-stage launcher command, progress, compile/simulation output, and final status. |
| `syn/<strategy>/<top>/run.log` | Per-design/strategy synthesis-stage transcript, including all tuning attempts. |
| `gate-level-sim/<strategy>/<top>/run.log` | Per-design/strategy gate-simulation stage transcript. |
| `testdata/test_reference.log` | Reference-test command and output. |
| `testdata/generate_vectors.log` | Generator command and output. |
| `rtl-sim/<top>/compile.log` | VCS compilation command and output. |
| `rtl-sim/<top>/sim.log` | Simulator invocation and output. |
| `syn/<strategy>/<top>/synth.log` | DC invocation/output; all tuning attempts from this invocation. |
| `gate-level-sim/<strategy>/<top>/compile.log` | Gate-level VCS compilation command and output. |
| `gate-level-sim/<strategy>/<top>/sim.log` | Gate-level simulation command and output. |

Every framework transcript uses this format, with **one space after `$`**:

```text
$ <shell-quoted command, including task-specific environment assignments>
<stdout and stderr merged into one stream>
```

Three blank lines separate successive command/output blocks, including in the
aggregate stage transcript. Raw tool output is not decorated with timestamps or severity prefixes. Each
stage transcript starts with the launcher command and `PENDING`, then records
`START`, tool output, and `DONE` or `FAIL`. Skipped stages explicitly record `SKIP`.
Thread-aware routing keeps parallel workers' output in their own stage logs;
all progress remains visible in the terminal. Individual tool transcripts remain
separate. A tuning log repeats the
command-header/output pair for successive attempts. Native SDF diagnostics use
`gate-level-sim/<strategy>/<top>/sdf_annotation.rpt`, not a command-transcript log.

### Generated Results And Overwrite Policy

| Location | Generated evidence |
|---|---|
| `syn/<strategy>/<top>/<top>_syn.{v,ddc,sdc,sdf}` | Selected netlist and matching tool exports. |
| `syn/<strategy>/<top>/<top>_*_report.txt` | Timing, area, power, units, and optional clock-gating reports. |
| `syn/<strategy>/<top>/<top>_report.txt` | Combined synthesis summary. |
| `syn/<strategy>/<top>/selected.json` | Current selection, convergence, settings, and artifact hashes. |
| `syn/<strategy>/<top>/search.json` | Ordered trial paths, statuses, periods, and metrics. |
| Simulation design folder: `completion.json` | Testbench counts, errors, latency, clock period, and completion status. |
| Simulation design folder: `verification.json` | Runner validation and provenance hashes. |
| Simulation design folder: `wave.vcd` | Current waveform for this design and strategy. |
| `result/summary.csv` | Current synthesis metrics and verification status for all nine combinations. |
| `result/tasks.json` | Most recent scheduled task states. |
| `syn/<strategy>/<top>/tune/001_period_10ns/` | Complete first trial: log, manifest, exports, reports, source list, and DC scratch. |
| `syn/<strategy>/<top>/tune/002_period_8.5ns/` | Next trial in chronological order; earlier trial files remain available. |
| `rtl-sim/<top>/work/` | Current RTL VCS build and scratch outputs. |
| `gate-level-sim/<strategy>/<top>/work/` | Current gate VCS build and native SDF annotation report. |
| `result/run.lock` | Lock file used by `flock`; its presence alone does not mean a run is active. |

Simulation design folders are `rtl-sim/<top>/` and `gate-level-sim/<strategy>/<top>/`.

Rerunning a task replaces its logs, validity record, completion record, and
waveform; temporary directories are reused without timestamps. Synthesis selections
are invalidated before reruns, even if dependencies later fail. Old exported files
may remain after failure, but their manifest is invalidated and they must not
be treated as a valid new selection. Other tasks' inputs and outputs are untouched.
Simulation scratch stays in `work/`; synthesis scratch stays in each numbered trial.
Only exact approved simulation work and synthesis tune paths may be automatically replaced; source directories,
published outputs, and symlink redirects are rejected by cleanup validation.

A new RTL run for `DP4` replaces its RTL-stage transcript and preserves the
`DP4_pipe` transcript and every synthesis/gate-stage transcript. A synthesis rerun
replaces only the requested design/strategy stage logs. Every requested stage's
log is initialized before scheduling, so a skipped stage cannot retain an old
success transcript. Reference preparation is shared and replaces its own log.

There is no combined coordinator transcript for single-design or `all` commands.
`collect` writes to the terminal and refreshes the aggregate CSV without creating
stage logs. The task-state file remains the latest scheduled invocation's record.
Legacy centralized work/log directories are neither used nor deleted automatically.

For commands, tuning, verification checks, and workflow details, see [Automation Framework](automation.md).

A requested synthesis stage removes and recreates its own `tune/` before scheduling,
even when a prerequisite later fails. A fixed-period run creates one numbered
trial. Tuning retains all trials until the next run; `selected.json` records the
relative selected trial path, such as `tune/001_period_10ns`. The stage publishes
copies of that trial's exports and reports beside the manifest. No separate
`attempt/` or `best/` directory is created. Trial numbering starts at `001` and
periods use decimal notation without redundant fractional zeros.
