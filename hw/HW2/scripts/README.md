# Hw2 Frontend Automation

## 1. Scope

Run on the course Linux server after loading VCS and Design Compiler. Python
3.6+ and `flock` are required. APR and the `compile_APR` / `gate_level/SYN`
directories are outside this workflow. **Physical design is not yet automated in Hw2.**
See the [project README](../README.md) for the architecture, physical-design flow,
and results.

## 2. Usage

From `HW2/`:

```bash
$ bash scripts/run.sh FXP_FMA pre
$ bash scripts/run.sh FXP_FMA synth Area --period 1
$ bash scripts/run.sh FXP_FMA tune all --jobs 3
$ bash scripts/run.sh FXP_FMA post all --jobs 3
$ bash scripts/run.sh all all --jobs 4
$ bash scripts/run.sh collect
```

`all all` skips targets whose `rtl/` folder has no `.v` files. Explicitly requesting
an unimplemented target reports an error. All three targets are implemented.
For multiple RTL files, an optional `rtl/files.txt` lists their relative paths in
analysis order. Otherwise the runner discovers `.v` files under `rtl/`.

The target folders and actual DUT names are different:

| Target folder | DUT top | Testbench |
|---|---|---|
| `FXP_FMA` | `FXP_FMA` | `TB_FXP_FMA.v` |
| `FLP_FMA` | `FMA` | `TB_FMA.v` |
| `FLP_FMA_4` | `FMA_pipe` | `TB_FMA_pipe.v` |

## 3. Files And Parallel Runs

Each target has one authoritative `compile/dc.tcl`. Edit that file to change the
synthesis setup. It is based on the supplied HW1 TA Tcl: the original libraries,
corners, ZeroWireload model, `set_max_area 0`, `compile`, naming rules, low-effort
power report, and DDC/Verilog/SDF 2.1/SDC export commands are retained.

The script changes source/output paths and top names, applies the TA's virtual
clock/max-delay option to combinational targets, and retains the physical clock
constraints for FMA_pipe. Library time units are converted to/from ns. Full reports
are kept alongside each synthesis attempt. Literal brackets and the cell-name
mapping in the TA naming rule are corrected for Tcl syntax.

| Location inside each target | Contents |
|---|---|
| `pre_sim/` | Testbench, `normal/`, and FLP-only `special/`; generated RTL simulation outputs |
| `post_sim/` | One identical testbench copy and shared input/golden folders |
| `post_sim/Area`, `Between`, `Delay` | Generated simulation outputs only |
| `compile/<strategy>/runs/<session>/roundNNN` | Isolated Tcl/WORK directory for each attempt |
| `gate_level/<strategy>/runs/<session>/roundNNN` | Attempt reports and exports |
| `gate_level/<strategy>/` | Selected `<top>.v`, `.sdf`, `.ddc`, `.sdc`, `selected.json`, `search.json` |

Post-sim reads the shared inputs with `+DATA_DIR=.../post_sim`, while writing
`ans_normal.txt`, `report_normal.txt`, FLP-only special outputs, waveform,
`tb_result.json`, and `verification.json` into its strategy directory. Source and
input folders are never cleared. Pre/post testbench sources are byte-identical.

`--jobs` limits the number of simultaneous tasks/tool processes. Area and Delay
searches can overlap; Between waits for both selected results. Each strategy's
post-sim waits for its synthesis. Failed dependencies are skipped, while unrelated
tasks continue. The launcher lock prevents two batch commands from sharing outputs.

## 4. Timing, Verification, And Results

The period search starts at 10 ns for Area and 0.01 ns for Delay. Between starts
at the midpoint of their selected periods and does not go below it. The next
proposal is `period - slack`, with a failing/passing bracket fallback. Defaults:
0.001 ns resolution, 0.005 ns slack tolerance, 20 attempts, 1000 ns period limit.
Change them with the corresponding `--area-seed`, `--delay-seed`, `--resolution`,
`--tolerance`, `--max-attempts`, and `--max-period` options. These searches are not
a guarantee of globally minimum area or delay; all use the TA compile settings.

The checker cycle defaults to 100 ns and can be changed with `--cycle`. It is
independent of the synthesis period. A post-sim pass at 100 ns does not verify
operation at a shorter tuned period. FMA_pipe uses synchronous active-high reset
and four capture edges, matching the testbench's `LATENCY = 4` schedule.
Its output is sampled at `#1` after the rising edge; changes to pipeline depth
or gate clock-to-Q timing require the checker schedule to be reviewed.

FXP checks 200 vectors; FLP checks 200 normal and 15 special vectors separately.
The runner requires a complete `tb_result.json` with zero errors and a nonempty
waveform. It clears only old generated completion/wave/annotation files before a
run, so an early `$finish` cannot reuse an earlier pass. Post-sim also requires
completed SDF annotation with zero errors, rejects timing diagnostics, and accepts
only the known connectivity warnings explicitly confirming annotation still occurs.

Use `--wave vcd` (default) or `--wave fsdb`. FSDB needs the course Verdi integration;
additional VCS integration flags can be supplied through `HW2_VCS_FSDB_FLAGS`.
Optional environment settings are `HW2_CELL_MODEL` and `HW2_LIBRARY_ROOT`.

Coordinator history is in `HW2/result/run.log`; full tool output is beside
each simulation or synthesis run. Normal progress, warnings, and failures appear in the terminal;
`--verbose` shows every tool-output line. Interruption terminates active tools and
leaves completed artifacts intact.

`result/summary.csv` records PPA, periods/slack, and verification status.
`result/pipeline_delays.csv` records measured stage delays. Stale selections have
blank measurements; unverified selections are labeled. Latency preserves HW1's
TA convention `reported_delay * declared_stage_count`, not measured end-to-end time.

`FLP_FMA_4/compile/stage_paths.tcl` defines the four timing boundaries using the
implemented register names. Synthesis requires a nonempty timing report for each
boundary before accepting the pipeline result. Missing stage reports cannot be
reconstructed by `collect`; the pipeline must be synthesized again.

### 4.1 Normal-Only FLP Testing

Use `--suite normal` for either FLP target in pre, post, or the full workflow:

```bash
$ bash scripts/run.sh FLP_FMA pre --suite normal
$ bash scripts/run.sh FLP_FMA_4 post all --suite normal --jobs 3
$ bash scripts/run.sh FLP_FMA all all --suite normal --jobs 3
```

The default `--suite all` checks 200 normal and 15 special vectors. Normal-only checks 200 vectors with exact bit equality and does not read special inputs. Direct VCS runs can pass `+NORMAL_ONLY`. Verification records store the selected suite; summary statuses show `passed(normal)` and remain unverified for full coverage until both suites pass. FXP always checks normal inputs.

### 4.2 Logs And Report Precision

Tool logs begin with `$ <shell-quoted command>` followed by the combined stdout/stderr streamed during execution:

`result/run.log` records `$ bash scripts/run.sh ...` at the start of each
invocation and the same `$` command header for every tool, alongside its working
directory. Synthesis headers include the task-specific environment variables.

`summary.csv` and `pipeline_delays.csv` display measured numbers with three
decimal places. JSON selections/search records retain their original precision.
Power columns remain in watts, so values below 0.0005 W display as `0.000`.

- Pre-simulation: `<design>/pre_sim/pre_sim.log`.
- Post-simulation: `<design>/post_sim/<strategy>/post_sim.log`.
- Synthesis attempts: `<design>/compile/<strategy>/runs/<session>/roundNNN/synth.log`.

Commands run from the directory containing their tool log. Terminal progress and diagnostics remain available, with `--verbose` showing all tool lines. Coordinator messages go to `result/run.log`; task statuses go to `result/session.json`, and the launcher lock is `result/run.lock`. New runs do not write to `log/`; existing historical logs are retained.
