"""Coordinate HW3 frontend tools; PrimeTime is deliberately a manual step.

State is replaced in fixed locations. A dependency graph prevents post-simulation
from consuming failed synthesis, while independent tops can still make progress.
"""

import argparse
import csv
import math
import os
import re
import shutil
import signal
import sys
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import helper as h


def stage_log_path(task):
    """Map a scheduler task to its stage-local aggregate transcript."""
    if task == 'reference':
        return h.ROOT / 'testdata/run.log'
    parts = task.split('/')
    if len(parts) == 2 and parts[1] == 'pre':
        return h.stage_directory(parts[0], 'pre') / 'run.log'
    h.require(len(parts) == 3, 'Invalid stage task: ' + task)
    return h.stage_directory(parts[0], parts[2], parts[1]) / 'run.log'


def preparation():
    """Check the oracle and regenerate the shared workload once per invocation."""
    for script in ('test_reference', 'generate_vectors'):
        h.run_command([sys.executable, '-B', str(h.ROOT / 'testdata' / (script + '.py'))],
                      h.ROOT, h.ROOT / 'testdata' / (script + '.log'), eda=False)


def selection_path(top, strategy):
    """Locate the authoritative manifest for a published synthesis candidate."""
    return h.stage_directory(top, 'synth', strategy) / 'selected.json'


def selected(top, strategy, converged=True):
    """Validate selected synthesis against current sources and exact exports."""
    path = selection_path(top, strategy)
    h.require(path.is_file(), 'No selection for {} {}; run synth or tune'.format(top, strategy))
    record = h.load_json(path)
    h.require(record.get('status') == 'selected', 'Synthesis selection is not complete')
    h.require(record['source_hash'] == h.source_hash(top), 'Stale RTL/settings; rerun synthesis')
    if converged:
        h.require(record.get('converged'), 'Selection is an unconverged fallback')

    for name, digest in record['artifact_hashes'].items():
        artifact = path.parent / name
        h.require(artifact.is_file() and h.file_hash(artifact) == digest, 'Selected export changed: ' + name)
    return record


def reset_synthesis(top, strategy):
    """Invalidate selection and replace only this stage's previous trial history."""
    h.save_json(selection_path(top, strategy), {'status': 'pending'})
    h.fresh_directory(h.tune_directory(top, strategy))
    directory = h.stage_directory(top, 'synth', strategy)
    (directory / 'synth.log').write_text('')
    h.save_json(directory / 'search.json', {'converged': False, 'attempts': []})


def synthesis(top, strategy, period, index=1):
    """Run one DC trial, retaining all outputs and recording errors or interruption."""
    directory = h.stage_directory(top, 'synth', strategy)
    work = h.trial_directory(top, strategy, index, period)
    work.mkdir(parents=True, exist_ok=False)
    record = {'top': top, 'strategy': strategy, 'index': index,
              'trial': work.relative_to(directory).as_posix(),
              'period_ns': float(period), 'status': 'running'}
    log = work / 'synth.log'
    h.save_json(work / 'trial.json', record)
    try:
        sources = h.implemented(top)
        fingerprint = h.source_hash(top)
        record['source_hash'] = fingerprint
        source_list = work / 'rtl_files.txt'
        source_list.write_text('\n'.join(map(str, sources)) + '\n')
        environment = {'TOP': top, 'SYN_CONFIG': strategy, 'PERIOD_NS': str(period),
                       'HW3_OUT': str(work), 'HW3_RTL_LIST': str(source_list)}
        h.run_command([h.tool('dcnxt_shell'), '-f', str(h.ROOT / 'syn/dc.tcl')],
                      work, log, environment)
        output = log.read_text(errors='replace')
        h.require(not re.search(r'(?im)^\s*(?:Error:|Error-|HW3_DC_ERROR:)', output),
                  'DC reported an error')
        h.require('HW3_DC_COMPLETE:' in output, 'DC did not reach completion')
        h.require(fingerprint == h.source_hash(top), 'Sources changed during synthesis')
        for extension in ('v', 'ddc', 'sdf', 'sdc'):
            artifact = work / (top + '_syn.' + extension)
            h.require(artifact.is_file() and artifact.stat().st_size > 0,
                      'Missing export: ' + str(artifact))
        record['metrics'] = h.metrics(work, top, period)
        record['status'] = 'passed' if record['metrics']['slack_ns'] >= 0 else 'timing_violated'
        return record
    except BaseException as error:
        record['status'] = 'interrupted' if h.CANCELLED.is_set() or isinstance(
            error, KeyboardInterrupt) else 'failed'
        record['error'] = str(error) or type(error).__name__
        with log.open('a', encoding='utf-8') as sink:
            sink.write('FAIL: ' + record['error'] + '\n')
        raise
    finally:
        h.save_json(work / 'trial.json', record)
        # Keep a detailed combined DC log as well as each individual trial log.
        if log.exists():
            with (directory / 'synth.log').open('a', encoding='utf-8') as sink:
                sink.write(log.read_text(errors='replace'))


def search_entry(record):
    """Expose trial identity, status and measurements in the ordered search index."""
    entry = {key: record[key] for key in ('index', 'trial', 'period_ns', 'status')}
    entry.update(record.get('metrics', {}))
    if 'error' in record:
        entry['error'] = record['error']
    return entry


def failed_trial(top, strategy, period, index, attempts, error):
    """Retain a failed trial in the search and prevent reuse of old selections."""
    directory = h.stage_directory(top, 'synth', strategy)
    trial = h.trial_directory(top, strategy, index, period)
    if (trial / 'trial.json').is_file():
        attempts.append(search_entry(h.load_json(trial / 'trial.json')))
    h.save_json(directory / 'search.json', {'converged': False, 'attempts': attempts})
    h.save_json(selection_path(top, strategy),
                {'status': 'failed', 'error': str(error) or type(error).__name__})


def publish(record, converged):
    """Copy a selected preserved trial into the stable design output directory."""
    top, strategy = record['top'], record['strategy']
    destination = h.stage_directory(top, 'synth', strategy)
    trial = destination / record['trial']
    h.require(trial.resolve() == trial.absolute() and trial.parent == h.tune_directory(top, strategy),
              'Invalid selected trial path')
    h.require(record['source_hash'] == h.source_hash(top), 'Sources changed before publication')
    hashes = {}
    for source in trial.glob(top + '_*'):
        if source.is_file():
            shutil.copyfile(str(source), str(destination / source.name))
            hashes[source.name] = h.file_hash(destination / source.name)
    result = dict(record, status='selected', converged=converged, artifact_hashes=hashes)
    h.save_json(selection_path(top, strategy), result)
    return result


def tune(top, strategy, args, prepared=False):
    """Search bounded periods, retaining passing evidence and recording every step."""
    if not prepared:
        reset_synthesis(top, strategy)
    h.save_json(selection_path(top, strategy), {'status': 'running'})
    floor = args.resolution
    if strategy == 'mid':
        parents = [selected(top, name) for name in ('area', 'delay')]
        area, delay = [Decimal(str(p['metrics']['period_ns'])) for p in parents]
        h.require(area >= delay, 'Area period is below delay period; inspect selections')
        floor = (area + delay) / 2
        period = floor
    else:
        period = args.area_seed if strategy == 'area' else args.delay_seed

    # Decimal quantization keeps search resolution reproducible across runs.
    def quantize(value):
        """Round a proposed period upward to a legal resolution multiple."""
        return (value / args.resolution).to_integral_value(rounding=ROUND_CEILING) * args.resolution

    attempts, seen, best = [], set(), None
    converged = False
    search_path = h.stage_directory(top, 'synth', strategy) / 'search.json'

    for number in range(args.max_attempts):
        period = quantize(max(floor, period))
        if period > args.max_period or period in seen:
            break
        seen.add(period)
        try:
            record = synthesis(top, strategy, period, index=number + 1)
        except BaseException as error:
            failed_trial(top, strategy, period, number + 1, attempts, error)
            raise
        measurements = record['metrics']
        attempts.append(search_entry(record))
        h.save_json(search_path, {'converged': False, 'attempts': attempts})
        slack = Decimal(str(measurements['slack_ns']))

        if slack >= 0:
            if best is None or period < Decimal(str(best['metrics']['period_ns'])):
                best = record
            if slack <= args.tolerance or (strategy == 'mid' and period == quantize(floor)):
                converged = True
                break

        high = Decimal(str(best['metrics']['period_ns'])) if best else None
        failures = [Decimal(str(m['period_ns'])) for m in attempts
                    if m['slack_ns'] < 0 and (high is None or Decimal(str(m['period_ns'])) < high)]
        low = max(failures) if failures else None
        if low is not None and high is not None and high - low <= args.resolution:
            converged = True
            break

        proposal = quantize(max(floor, period - slack))
        if low is not None and high is not None:
            if not low < proposal < high or proposal in seen:
                proposal = quantize((low + high) / 2)
        if proposal in seen:
            proposal = period + args.resolution if slack < 0 else quantize(max(floor, period - args.resolution))
        period = proposal

    h.save_json(search_path, {'converged': converged, 'attempts': attempts})
    if best:
        publish(best, converged)
    h.require(converged, 'Period search did not converge; any passing fallback is marked unconverged')


def single_synthesis(top, strategy, args, prepared=False):
    """Run one numbered trial and publish it only if setup timing passes."""
    if not prepared:
        reset_synthesis(top, strategy)
    h.save_json(selection_path(top, strategy), {'status': 'running'})
    attempts = []
    try:
        result = synthesis(top, strategy, args.period)
    except BaseException as error:
        failed_trial(top, strategy, args.period, 1, attempts, error)
        raise
    passed = result['metrics']['slack_ns'] >= 0
    h.save_json(h.stage_directory(top, 'synth', strategy) / 'search.json',
                {'converged': passed, 'attempts': [search_entry(result)]})
    if not passed:
        h.save_json(selection_path(top, strategy), {'status': 'failed'})
    h.require(passed, 'Fixed-period synthesis violates setup timing')
    publish(result, True)


def verification_path(top, strategy=None):
    """Locate the current simulation validity record for this task."""
    return h.stage_directory(top, 'post' if strategy else 'pre', strategy) / 'verification.json'


def invalidate_simulation(top, strategy=None):
    """Remove this task's old generated evidence before it can fail or be skipped."""
    directory = h.stage_directory(top, 'post' if strategy else 'pre', strategy)
    h.save_json(verification_path(top, strategy), {'status': 'pending'})

    for name in ('compile.log', 'sim.log', 'completion.json', 'wave.vcd', 'sdf_annotation.rpt'):
        path = directory / name
        if path.exists():
            path.unlink()


def simulation(top, args, strategy=None):
    """Compile and simulate with fresh completion, waveform, and provenance checks."""
    post = strategy is not None
    directory = h.stage_directory(top, 'post' if post else 'pre', strategy)
    bench = h.ROOT / ('gate-level-sim' if post else 'rtl-sim') / 'TB.v'
    validity_path = verification_path(top, strategy)
    invalidate_simulation(top, strategy)
    h.save_json(validity_path, {'status': 'running'})

    fingerprint, inputs = h.source_hash(top), h.input_hash(post)
    latency = h.TOPS[top]
    period = args.cycle or Decimal('20')
    candidate = None
    cell = None

    if post:
        candidate = selected(top, strategy)
        period = args.cycle or Decimal(str(candidate['metrics']['period_ns']))
        exports = h.stage_directory(top, 'synth', strategy)
        cell = Path(os.environ.get('HW3_CELL_MODEL', h.CELL_MODEL)).resolve()
        h.require(cell.is_file(), 'Missing standard-cell model: ' + str(cell))
        cell_fingerprint = h.file_hash(cell)
        sources = [exports / (top + '_syn.v'), cell]
    else:
        sources = h.implemented(top)

    work = h.fresh_directory(h.work_directory(top, 'post' if post else 'pre', strategy))
    executable = work / 'simv'
    command = [h.tool('vcs'), '-full64', '+v2k', '-debug_access+all', '-top', 'testbench',
               '+define+DUT_MODULE=' + top, '+define+DUT_LATENCY=' + str(latency),
               '+define+TB_PERIOD=' + str(period), '-o', str(executable), '-Mdir=' + str(work / 'csrc')]
    command += ['+incdir+' + str(p) for p in sorted({p.parent for p in sources})]
    if post:
        # VCS requires the annotation filename to be known during compilation.
        sdf_file = str(exports / (top + '_syn.sdf'))
        command += ['+neg_tchk', '+define+HW3_GATE_SIM=1',
                    '+define+HW3_SDF_FILE="' + sdf_file + '"']
    command += [str(bench)] + list(map(str, sources))
    h.run_command(command, work, directory / 'compile.log')
    h.require(executable.is_file(), 'VCS produced no simulator executable')

    completion = directory / 'completion.json'
    wave = directory / 'wave.vcd'
    annotation = directory / 'sdf_annotation.rpt'
    command = [str(executable), '+VECTORS=' + str(h.ROOT / 'testdata/vectors.txt'),
               '+RESULT=' + str(completion), '+VCD=' + str(wave)]
    if post:
        command += ['+GATE', '+SDF=' + str(exports / (top + '_syn.sdf'))]
    log = directory / 'sim.log'
    h.run_command(command, work, log)

    # VCS requires a literal fourth argument to $sdf_annotate. Keep the native
    # fixed-name report isolated in work, then publish the top/strategy report.
    if post:
        native_annotation = work / 'sdf_annotation.rpt'
        h.require(native_annotation.is_file(), 'Missing native SDF annotation report')
        shutil.copyfile(str(native_annotation), str(annotation))

    rows = (h.ROOT / 'testdata/vectors.txt').read_text().splitlines()
    h.require(rows and all(len(row.split()) == 10 for row in rows), 'Invalid generated vector file')
    result = h.load_json(completion)
    h.validate_completion(result, len(rows), latency, period)
    text = log.read_text(errors='replace')
    h.require(re.search(r'(?m)^PASS:', text) and not re.search(r'(?m)^FAIL:', text), 'Missing PASS or observed FAIL')
    h.require(not re.search(r'(?im)^\s*(?:Error|Fatal)(?:-|:)', text), 'Simulator error/fatal')
    h.require(wave.is_file() and wave.stat().st_size > 0, 'Missing or empty VCD')
    if post:
        h.validate_sdf(annotation, text)
        h.require(cell_fingerprint == h.file_hash(cell), 'Cell model changed during simulation')
        # Revalidate exports, including changes made during tool execution.
        h.require(selected(top, strategy)['artifact_hashes'] == candidate['artifact_hashes'], 'Synthesis changed during simulation')

    h.require(fingerprint == h.source_hash(top) and inputs == h.input_hash(post), 'Sources or vectors changed during simulation')
    result.update(source_hash=fingerprint, input_hash=inputs, waveform_hash=h.file_hash(wave),
                  waveform=str(wave.relative_to(h.ROOT)))
    if post:
        result.update(artifact_hashes=candidate['artifact_hashes'], cell_model=str(cell),
                      cell_model_hash=h.file_hash(cell),
                      at_selected_period=math.isclose(float(period), candidate['metrics']['period_ns'], abs_tol=1e-9))
    h.save_json(validity_path, result)
    print('PASS {} {} at {} ns'.format(top, strategy or 'pre', period))


def verification_current(top, strategy=None, candidate=None):
    """Distinguish absent, failed, stale, slow-clock, and currently valid evidence."""
    path = verification_path(top, strategy)
    if not path.exists():
        return 'pending'
    record = h.load_json(path)
    if record.get('status') != 'passed':
        return record.get('status', 'failed')
    if record.get('source_hash') != h.source_hash(top) or record.get('input_hash') != h.input_hash(bool(strategy)):
        return 'stale'
    wave = h.ROOT / record['waveform']
    if not wave.is_file() or h.file_hash(wave) != record['waveform_hash']:
        return 'stale'
    if strategy:
        if candidate is None or record.get('artifact_hashes') != candidate['artifact_hashes']:
            return 'stale'
        cell = Path(record['cell_model'])
        if not cell.is_file() or h.file_hash(cell) != record['cell_model_hash']:
            return 'stale'
        if not record.get('at_selected_period'):
            return 'passed_other_period'
    return 'passed'


def collect():
    """Replace summary.csv from current evidence without executing EDA tools."""
    rows = []
    for top, latency in h.TOPS.items():
        for strategy in h.STRATEGIES:
            record = None
            status = 'pending'
            if selection_path(top, strategy).exists():
                try:
                    record = selected(top, strategy, converged=False)
                    status = 'selected' if record.get('converged') else 'unconverged'
                except (RuntimeError, KeyError, OSError, ValueError):
                    status = 'stale/failed'

            row = dict(top=top, strategy=strategy, synthesis_status=status,
                       converged=bool(record and record.get('converged')), latency_cycles=latency)
            if record:
                row.update(record['metrics'])
                # DP4 is combinational; cycle latency is meaningful for pipelines.
                row['latency_ns'] = record['metrics']['period_ns'] * latency if latency else ''
            for key, mode in [('pre_status', None), ('post_status', strategy)]:
                try:
                    row[key] = verification_current(top, mode, record)
                except (RuntimeError, KeyError, OSError, ValueError):
                    row[key] = 'stale/failed'
            row['status'] = ('verified' if status == 'selected' and row['pre_status'] == 'passed'
                             and row['post_status'] == 'passed' else 'unverified')
            rows.append(row)

    path = h.ROOT / 'result/summary.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ['top', 'strategy', 'comb_um2', 'seq_um2', 'total_um2', 'delay_ns', 'period_ns',
               'slack_ns', 'latency_cycles', 'latency_ns', 'dynamic_uw', 'leakage_uw', 'power_uw',
               'converged', 'synthesis_status', 'pre_status', 'post_status', 'status']
    with path.open('w', newline='') as output:
        writer = csv.DictWriter(output, columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print('Collected current evidence into result/summary.csv')


def run_tasks(tasks, jobs, logs=None):
    """Route worker output to stage logs while executing the dependency graph."""
    with h.stage_output() as router:
        execute_tasks(tasks, jobs, router, logs or {})


def execute_tasks(tasks, jobs, router, logs):
    """Execute bounded workers, recording success, failure and skipped stages."""
    def execute(key, function):
        """Keep one worker's progress, commands, stdout and stderr in its log."""
        with router.capture(logs.get(key)):
            print('START ' + key)
            try:
                function()
            except BaseException as error:
                print('FAIL {}: {}'.format(key, error))
                raise
            print('DONE ' + key)

    def skip(key, reason):
        """Record an explicit terminal status for a stage that did not start."""
        with router.capture(logs.get(key)):
            print('SKIP {}: {}'.format(key, reason))

    pending, active, states = OrderedDict(tasks), {}, {}
    executor = ThreadPoolExecutor(max_workers=jobs)
    try:
        while pending or active:
            for key, (function, dependencies) in list(pending.items()):
                if any(states.get(dep) in ('failed', 'skipped') for dep in dependencies):
                    states[key] = 'skipped'
                    del pending[key]
                    skip(key, 'prerequisite failed or skipped')
                elif len(active) < jobs and all(states.get(dep) == 'passed' for dep in dependencies):
                    active[executor.submit(execute, key, function)] = key
                    del pending[key]
            if not active:
                h.require(not pending, 'Unresolved task dependencies')
                break
            done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                key = active.pop(future)
                try:
                    future.result()
                    states[key] = 'passed'
                except Exception:
                    states[key] = 'failed'
    except BaseException:
        h.terminate_processes()
        for key in pending:
            states[key] = 'skipped'
            skip(key, 'invocation interrupted')
        for future, key in active.items():
            if future.cancel():
                states[key] = 'skipped'
                skip(key, 'invocation interrupted before start')
        raise
    finally:
        executor.shutdown(wait=True)
        for future, key in active.items():
            if key not in states:
                states[key] = 'failed' if future.exception() else 'passed'
        h.save_json(h.ROOT / 'result/tasks.json', states)
    h.require(all(value == 'passed' for value in states.values()), 'Some tasks failed/skipped; see result/tasks.json')


def dispatch(args):
    """Build task dependencies from the selected action, tops, and strategies."""
    if args.target == 'collect':
        collect()
        return
    tops = list(h.TOPS) if args.target == 'all' else [args.target]
    strategies = list(h.STRATEGIES) if args.strategy == 'all' else [args.strategy]
    tuning = args.action in ('tune', 'all')
    if tuning and 'mid' in strategies:
        strategies = list(h.STRATEGIES)

    tasks = OrderedDict()
    if args.action in ('pre', 'post', 'all'):
        tasks['reference'] = (preparation, [])
    for top in tops:
        pre = top + '/pre'
        if args.action in ('pre', 'all'):
            tasks[pre] = (lambda t=top: simulation(t, args), ['reference'])
        for strategy in strategies:
            synth = top + '/' + strategy + '/synth'
            if args.action in ('synth', 'tune', 'all'):
                dependencies = [pre] if args.action == 'all' else []
                if tuning and strategy == 'mid':
                    dependencies += [top + '/' + parent + '/synth' for parent in ('area', 'delay')]
                function = (lambda t=top, s=strategy: tune(t, s, args, prepared=True)) if tuning else (
                    lambda t=top, s=strategy: single_synthesis(t, s, args, prepared=True))
                tasks[synth] = (function, dependencies)
            if args.action in ('post', 'all'):
                dependencies = [synth] if args.action == 'all' else ['reference']
                tasks[top + '/' + strategy + '/post'] = (
                    lambda t=top, s=strategy: simulation(t, args, s), dependencies)

    # Initialize every requested stage log, including stages later skipped by
    # dependencies. Existing logs for other stages/designs remain untouched.
    invocation = os.environ.get('HW3_RUN_COMMAND') or h.command_text([sys.executable] + sys.argv)
    logs = {key: stage_log_path(key) for key in tasks}
    for key, path in logs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('$ ' + invocation.rstrip() + '\nPENDING ' + key + '\n', encoding='utf-8')

    # Invalidate requested results before scheduling, including tasks that will
    # be skipped because a prerequisite fails. Old evidence cannot masquerade as new.
    for key in tasks:
        parts = key.split('/')
        if parts[-1] == 'synth':
            reset_synthesis(parts[0], parts[1])
        elif parts[-1] in ('pre', 'post'):
            strategy = parts[1] if parts[-1] == 'post' else None
            invalidate_simulation(parts[0], strategy)
    run_tasks(tasks, args.jobs, logs)


def parse_args(argv=None):
    """Parse the public CLI and reject invalid numeric settings before tools run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=list(h.TOPS) + ['all', 'collect'])
    parser.add_argument('action', nargs='?', choices=['pre', 'synth', 'tune', 'post', 'all'], default='all')
    parser.add_argument('strategy', nargs='?', choices=list(h.STRATEGIES) + ['all'], default='all')
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--period', type=Decimal, default=Decimal('20'))
    parser.add_argument('--cycle', type=Decimal, default=None, help='Simulation period in ns; post defaults to selected period')
    parser.add_argument('--area-seed', type=Decimal, default=Decimal('10'))
    parser.add_argument('--delay-seed', type=Decimal, default=Decimal('0.01'))
    parser.add_argument('--resolution', type=Decimal, default=Decimal('0.001'))
    parser.add_argument('--tolerance', type=Decimal, default=Decimal('0.005'))
    parser.add_argument('--max-attempts', type=int, default=20)
    parser.add_argument('--max-period', type=Decimal, default=Decimal('1000'))
    args = parser.parse_args(argv)
    for name in ('period', 'cycle', 'area_seed', 'delay_seed', 'resolution', 'max_period'):
        number = getattr(args, name)
        if number is not None and (not number.is_finite() or number <= 0):
            parser.error(name + ' must be finite and positive')
    if args.jobs < 1 or args.max_attempts < 1 or not args.tolerance.is_finite() or args.tolerance < 0:
        parser.error('jobs/attempts must be positive; tolerance must be finite and nonnegative')
    return args


def main():
    """Handle signals and collection; task transcripts live in stage directories."""
    args = parse_args()
    (h.ROOT / 'result').mkdir(parents=True, exist_ok=True)

    def interrupt(signum, frame):
        """Convert terminal termination into coordinated child-process cleanup."""
        raise KeyboardInterrupt('Signal ' + str(signum))

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    code = 0
    try:
        dispatch(args)
    except KeyboardInterrupt:
        h.terminate_processes()
        print('FAIL: interrupted; incomplete tasks are not verified')
        code = 130
    except Exception as error:
        print('FAIL: ' + str(error))
        code = 1
    finally:
        try:
            if args.target != 'collect':
                collect()
        except Exception as error:
            print('FAIL: collection: ' + str(error))
            code = code or 1
    return code


if __name__ == '__main__':
    sys.exit(main())
