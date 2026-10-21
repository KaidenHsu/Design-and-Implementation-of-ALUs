#!/usr/bin/env python3
"""HW2 pre-sim, period tuning, post-sim, and reports. APR stays separate."""

import argparse
import csv
import logging
import os
import shlex
import shutil
import signal
import sys
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import helper as h

SESSION = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]


def tool(name):
    executable = shutil.which(name)
    h.require(executable, 'Missing {}; initialize the course EDA environment'.format(name))
    return executable


def synthesis(design, optimization, period, number, session=SESSION):
    """Every attempt has its own WORK directory, reports, and exports."""
    root = h.ROOT / design
    work = root / 'compile' / optimization / 'runs' / session / ('round%03d' % number)
    output = root / 'gate_level' / optimization / 'runs' / session / ('round%03d' % number)
    work.mkdir(parents=True)
    output.mkdir(parents=True)
    shutil.copyfile(str(root / 'compile' / 'dc.tcl'), str(work / 'dc.tcl'))
    sources = h.rtl_files(design)
    source_hash = h.fingerprint(design)
    (work / 'rtl_files.txt').write_text('\n'.join(map(str, sources)) + '\n')
    log = work / 'synth.log'
    environment = {'HW2_DESIGN': design, 'HW2_DESIGN_ROOT': str(root),
                   'HW2_PERIOD': str(period), 'HW2_OUT': str(output),
                   'HW2_RTL_LIST': str(work / 'rtl_files.txt')}
    h.LOG.info('%s %s synthesis #%d: target %s ns', design, optimization, number, period)
    h.run_command([tool('dcnxt_shell'), '-f', 'dc.tcl'], work, log, environment)
    h.require(source_hash == h.fingerprint(design), 'RTL/Tcl changed during synthesis')
    top = h.DESIGNS[design][0]
    for extension in ('v', 'sdf', 'ddc', 'sdc'):
        path = output / (top + '.' + extension)
        h.require(path.is_file() and path.stat().st_size, 'Missing export ' + str(path))
    result = {'design': design, 'optimization': optimization,
              'directory': str(output.relative_to(h.ROOT)),
              'metrics': h.metrics(output, period), 'source_hash': source_hash}
    if h.DESIGNS[design][2] > 1:
        scale = h.time_scale(output)
        # Reject empty/unparseable reports before publishing a selection.
        result['stage_delays_ns'] = [
            h.timing(output / ('timing_report_stage%d.txt' % stage), scale)['delay_ns']
            for stage in range(1, h.DESIGNS[design][2] + 1)]
    h.save_json(output / 'result.json', result)
    h.LOG.info('%s %s #%d: slack=%.6g ns area=%.3f um2', design, optimization,
               number, result['metrics']['slack_ns'], result['metrics']['total_um2'])
    return result


def selected(design, optimization):
    path = h.ROOT / design / 'gate_level' / optimization / 'selected.json'
    h.require(path.is_file(), 'No selected {} {}; run tune first'.format(design, optimization))
    result = h.load_json(path)
    h.require(result['source_hash'] == h.fingerprint(design), 'RTL/Tcl changed; rerun synthesis')
    return result


def select(result):
    """Publish a passing candidate to the familiar gate_level/<strategy>/ paths."""
    design, optimization = result['design'], result['optimization']
    root = h.ROOT / design / 'gate_level' / optimization
    original = h.ROOT / result['directory']
    top = h.DESIGNS[design][0]
    hashes = {}
    for extension in ('v', 'sdf', 'ddc', 'sdc'):
        name = top + '.' + extension
        shutil.copyfile(str(original / name), str(root / name))
        hashes[name] = h.file_hash(root / name)
    result = dict(result, artifact_hashes=hashes)
    h.save_json(root / 'selected.json', result)
    return result


def tune(design, optimization, args):
    """Slack-guided period search with a bracket fallback and bounded attempts."""
    floor = Decimal('0.001')
    if optimization == 'Between':
        parents = [selected(design, opt) for opt in ('Area', 'Delay')]
        h.require(all(value.get('converged') for value in parents), 'Between requires converged Area/Delay')
        periods = [Decimal(str(value['metrics']['period_ns'])) for value in parents]
        h.require(periods[0] >= periods[1], 'Area period below Delay; inspect their selections')
        floor = sum(periods) / 2
        period = floor
    else:
        period = args.area_seed if optimization == 'Area' else args.delay_seed
    quantize = lambda value: (value / args.resolution).to_integral_value(rounding=ROUND_CEILING) * args.resolution
    attempts, seen, best, low = [], set(), None, None
    converged = False
    for number in range(1, args.max_attempts + 1):
        period = quantize(max(floor, period))
        h.require(period <= args.max_period, 'Period search exceeded --max-period')
        if period in seen:
            break
        seen.add(period)
        result = synthesis(design, optimization, period, number)
        attempts.append(result)
        slack = Decimal(str(result['metrics']['slack_ns']))
        if slack >= 0:
            if best is None or period < Decimal(str(best['metrics']['period_ns'])):
                best = result
            if slack <= args.tolerance or (optimization == 'Between' and period == quantize(floor)):
                converged = True
                break
        high = Decimal(str(best['metrics']['period_ns'])) if best else None
        # Recompute bounds: synthesis is not strictly monotonic across netlists.
        failed_periods = [Decimal(str(value['metrics']['period_ns'])) for value in attempts
                          if value['metrics']['slack_ns'] < 0 and
                          (high is None or Decimal(str(value['metrics']['period_ns'])) < high)]
        low = max(failed_periods) if failed_periods else None
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
    path = h.ROOT / design / 'gate_level' / optimization
    h.save_json(path / 'search.json', {'session': SESSION, 'converged': converged, 'attempts': attempts})
    if best:
        select(dict(best, converged=converged))
    h.require(converged, 'Search did not converge; any passing fallback is retained. Inspect search.json.')
    return best


def simulation(design, args, optimization=None):
    """Compile in .build/; run beside local data without deleting source folders."""
    top, bench, stages, expected = h.DESIGNS[design]
    root = h.ROOT / design
    post = optimization is not None
    source_directory = root / 'post_sim' if post else root / 'pre_sim'
    directory = source_directory / optimization if post else source_directory
    directory.mkdir(parents=True, exist_ok=True)
    h.require((source_directory / bench).is_file(), 'Missing testbench ' + str(source_directory / bench))
    suite = 'normal' if design == 'FXP_FMA' else args.suite
    expected = 200 if suite == 'normal' else expected
    input_hash = h.simulation_inputs(design, source_directory, suite)
    source_hash = h.fingerprint(design)
    if stages > 1:
        h.require(args.cycle > Decimal('2'), 'Pipeline checker samples at #1; use a cycle >2 ns')
    if post:
        candidate = selected(design, optimization)
        h.require(candidate.get('converged'), 'Selected synthesis has not converged')
        exports = root / 'gate_level' / optimization
        for name, digest in candidate['artifact_hashes'].items():
            h.require(h.file_hash(exports / name) == digest, 'Selected export changed: ' + name)
        cell = Path(os.environ.get('HW2_CELL_MODEL', h.CELL_MODEL))
        h.require(cell.is_file(), 'Missing standard cell model: ' + str(cell))
        sources = [exports / (top + '.v'), cell]
        h.LOG.info('%s %s post-sim: checker cycle=%s ns; synthesis target=%s ns',
                   design, optimization, args.cycle, candidate['metrics']['period_ns'])
    else:
        sources = h.rtl_files(design)
    build = directory / '.build' / SESSION
    build.mkdir(parents=True)
    # Clear only generated completion/wave/annotation files, never inputs or HDL.
    for name in ('tb_result.json', 'wave.vcd', 'wave.fsdb', 'sdf_annotation.log'):
        path = directory / name
        if path.exists():
            path.unlink()
    command = [tool('vcs'), '-R', '-full64', '+v2k', '-debug_access+all',
               '-top', 'testbench', '-o', str(build / 'simv'),
               '-Mdir=' + str(build / 'csrc'), '+define+CYCLE=' + str(args.cycle)]
    command += ['+incdir+' + str(path) for path in sorted({p.parent for p in sources})]
    if post:
        # The same source enables annotation only when this define is supplied.
        sdf = str(exports / (top + '.sdf')).replace('\\', '/')
        command += ['+define+SDF_FILE="' + sdf + '"', '-error=noMPD', '+neg_tchk']
    if args.wave == 'fsdb':
        command += ['+define+HW2_FSDB', '+vcs+fsdbon']
        command += shlex.split(os.environ.get('HW2_VCS_FSDB_FLAGS', ''))
    command += [str(source_directory / bench)] + list(map(str, sources))
    # Share post_sim inputs, but keep each strategy's generated outputs isolated.
    command += ['+DATA_DIR=' + str(source_directory)]
    if design != 'FXP_FMA' and suite == 'normal':
        command += ['+NORMAL_ONLY']
    log = directory / ('post_sim.log' if post else 'pre_sim.log')
    record_path = directory / 'verification.json'
    h.save_json(record_path, {'status': 'running', 'session': SESSION})
    try:
        h.run_command(command, directory, log)
        completion = h.load_json(directory / 'tb_result.json')
        h.require(completion.get('status') == 'passed' and completion.get('checked') == expected
                  and completion.get('errors') == 0, 'Testbench failed or did not finish all comparisons')
        h.require(completion.get('normal') == {'checked': 200, 'errors': 0}, 'Normal suite incomplete')
        if design != 'FXP_FMA' and suite == 'all':
            h.require(completion.get('special') == {'checked': 15, 'errors': 0}, 'Special suite incomplete')
        wave = directory / ('wave.' + args.wave)
        h.require(wave.is_file() and wave.stat().st_size > 0, 'Missing waveform output')
        if post:
            text = log.read_text(errors='replace')
            h.require('HW2_SDF_ANNOTATION_REQUESTED' in text, 'SDF annotation not requested')
            h.validate_sdf(directory / 'sdf_annotation.log', text)
        h.require(source_hash == h.fingerprint(design), 'RTL/Tcl changed during simulation')
        record = dict(completion, suite=suite, session=SESSION, source_hash=source_hash, cycle_ns=str(args.cycle),
                      waveform=str(wave.relative_to(h.ROOT)), input_hash=input_hash)
        h.require(input_hash == h.simulation_inputs(design, source_directory, suite), 'Test inputs changed during simulation')
        if post:
            record['artifact_hashes'] = candidate['artifact_hashes']
        h.save_json(record_path, record)
        h.LOG.info('%s %s PASS (%d vectors)', design, optimization or 'pre-sim', expected)
        return record
    except Exception as error:
        h.save_json(record_path, {'status': 'failed', 'session': SESSION, 'error': str(error)})
        raise


def collect():
    """Collect selections and stage timing without rerunning the EDA tools."""
    result_dir = h.ROOT / 'result'
    result_dir.mkdir(exist_ok=True)
    rows, pipeline = [], []
    for design, (top, bench, stages, expected) in h.DESIGNS.items():
        for optimization in h.OPTS:
            path = h.ROOT / design / 'gate_level' / optimization / 'selected.json'
            if not path.exists():
                continue
            value = h.load_json(path)
            try:
                current = value['source_hash'] == h.fingerprint(design)
            except RuntimeError:
                current = False
            exports = path.parent
            current = current and all((exports / name).is_file() and h.file_hash(exports / name) == digest
                                      for name, digest in value['artifact_hashes'].items())
            statuses = []
            for verification in (h.ROOT / design / 'pre_sim' / 'verification.json',
                                 h.ROOT / design / 'post_sim' / optimization / 'verification.json'):
                record = h.load_json(verification) if verification.exists() else {}
                passed = record.get('status') == 'passed' and record.get('source_hash') == value['source_hash']
                source_directory = h.ROOT / design / ('post_sim' if 'post_sim' in verification.parts else 'pre_sim')
                if passed:
                    passed = record.get('input_hash') == h.simulation_inputs(design, source_directory, record.get('suite', 'all'))
                if 'post_sim' in verification.parts:
                    passed = passed and record.get('artifact_hashes') == value['artifact_hashes']
                statuses.append(('passed(normal)' if design != 'FXP_FMA' and record.get('suite') == 'normal' else 'passed') if passed else 'pending/failed')
            metrics = value['metrics'] if current else {}
            row = dict(design=design, optimization=optimization, **metrics)
            row.update(status='verified' if current and statuses == ['passed', 'passed'] else
                       ('unverified' if current else 'stale'), converged=value.get('converged', False),
                       pre_status=statuses[0], post_status=statuses[1])
            # Preserve HW1/TA's reported latency convention, not a measured end-to-end latency.
            row['latency_ns'] = metrics['delay_ns'] * stages if metrics else ''
            rows.append(row)
            original = h.ROOT / value['directory']
            stage_rows = []
            if stages > 1 and current:
                for stage in range(1, stages + 1):
                    report = original / ('timing_report_stage%d.txt' % stage)
                    try:
                        delay = h.timing(report, h.time_scale(original))['delay_ns'] if report.exists() else ''
                    except (OSError, RuntimeError) as error:
                        h.LOG.warning('Stage report unavailable: %s', error)
                        delay = ''
                    stage_rows.append(dict(design=design, optimization=optimization, stage=stage, delay_ns=delay))
                measured = [r['delay_ns'] for r in stage_rows if r['delay_ns'] != '']
                for row_stage in stage_rows:
                    row_stage['critical'] = (row_stage['delay_ns'] == max(measured)
                                             if len(measured) == stages else '')
                if len(measured) != stages:
                    h.LOG.warning('%s %s: missing stage timing reports; rerun synthesis with the current stage mappings', design, optimization)
                pipeline.extend(stage_rows)
    columns = ['design', 'optimization', 'comb_um2', 'seq_um2', 'total_um2', 'delay_ns',
               'latency_ns', 'period_ns', 'slack_ns', 'dynamic_w', 'leakage_w', 'power_w',
               'status', 'converged', 'pre_status', 'post_status']
    for name, fields, data in [('summary.csv', columns, rows),
                               ('pipeline_delays.csv', ['design', 'optimization', 'stage', 'delay_ns', 'critical'], pipeline)]:
        with (result_dir / name).open('w', newline='') as output:
            writer = csv.DictWriter(output, fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(h.csv_row(row) for row in data)
    h.LOG.info('Collected %d selections into result/', len(rows))


def run_tasks(tasks, jobs):
    """Bounded DAG scheduler: skip failed dependencies, keep independent work."""
    pending, active, states = OrderedDict(tasks), {}, {}
    executor = ThreadPoolExecutor(max_workers=jobs)
    try:
        while pending or active:
            skipped = False
            for key, (function, dependencies) in list(pending.items()):
                if any(states.get(dep) in ('failed', 'skipped') for dep in dependencies):
                    states[key] = 'skipped'
                    del pending[key]
                    skipped = True
                    h.LOG.warning('SKIP %s: dependency failed', key)
                elif len(active) < jobs and all(states.get(dep) == 'passed' for dep in dependencies):
                    active[executor.submit(function)] = key
                    del pending[key]
                    h.LOG.info('START %s', key)
            if not active:
                if skipped and pending:
                    continue
                h.require(not pending, 'Task dependencies cannot make progress')
                break
            done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                key = active.pop(future)
                try:
                    future.result()
                    states[key] = 'passed'
                    h.LOG.info('DONE %s', key)
                except Exception as error:
                    states[key] = 'failed'
                    h.LOG.error('FAIL %s: %s', key, error)
    except BaseException:
        h.stop_processes()
        for future in active:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True)
    h.save_json(h.ROOT / 'result' / 'session.json', {'session': SESSION, 'tasks': states})
    h.require(all(state == 'passed' for state in states.values()), 'Some tasks failed/skipped; inspect result/session.json')


def dispatch(args):
    if args.design == 'collect':
        collect()
        return
    designs = list(h.DESIGNS) if args.design == 'all' else [args.design]
    if args.design == 'all':
        designs = [name for name in designs if list((h.ROOT / name / 'rtl').rglob('*.v'))]
        h.require(designs, 'No implemented RTL targets')
    optimizations = h.OPTS if args.optimization == 'all' else (args.optimization,)
    tasks = OrderedDict()
    for design in designs:
        pre = design + '/pre'
        if args.action in ('pre', 'all'):
            tasks[pre] = (lambda d=design: simulation(d, args), [])
        for optimization in optimizations:
            synth_key = design + '/' + optimization + '/synth'
            if args.action in ('synth', 'tune', 'all'):
                dependencies = [pre] if args.action == 'all' else []
                if optimization == 'Between' and args.optimization == 'all':
                    dependencies += [design + '/' + opt + '/synth' for opt in ('Area', 'Delay')]
                if args.action == 'synth':
                    def function(d=design, o=optimization):
                        value = synthesis(d, o, args.period, 1)
                        h.require(value['metrics']['slack_ns'] >= 0, 'Single synthesis violates timing')
                        return select(dict(value, converged=True))
                else:
                    function = lambda d=design, o=optimization: tune(d, o, args)
                tasks[synth_key] = (function, dependencies)
            if args.action in ('post', 'all'):
                tasks[design + '/' + optimization + '/post'] = (
                    lambda d=design, o=optimization: simulation(d, args, o),
                    [synth_key] if args.action == 'all' else [])
    run_tasks(tasks, args.jobs)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('design', choices=list(h.DESIGNS) + ['all', 'collect'])
    parser.add_argument('action', nargs='?', choices=['pre', 'synth', 'tune', 'post', 'all'], default='all')
    parser.add_argument('optimization', nargs='?', choices=list(h.OPTS) + ['all'], default='all')
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--period', type=Decimal, default=Decimal('1'))
    parser.add_argument('--cycle', type=Decimal, default=Decimal('100'))
    parser.add_argument('--area-seed', type=Decimal, default=Decimal('10'))
    parser.add_argument('--delay-seed', type=Decimal, default=Decimal('0.01'))
    parser.add_argument('--resolution', type=Decimal, default=Decimal('0.001'))
    parser.add_argument('--tolerance', type=Decimal, default=Decimal('0.005'))
    parser.add_argument('--max-period', type=Decimal, default=Decimal('1000'))
    parser.add_argument('--max-attempts', type=int, default=20)
    parser.add_argument('--wave', choices=['vcd', 'fsdb'], default='vcd')
    parser.add_argument('--suite', choices=['all', 'normal'], default='all',
                        help='FLP test datasets; FXP always uses normal inputs')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    for name in ('period', 'cycle', 'area_seed', 'delay_seed', 'resolution', 'max_period'):
        value = getattr(args, name)
        if not value.is_finite() or value <= 0:
            parser.error(name + ' must be finite and positive')
    if args.jobs < 1 or args.max_attempts < 1 or not args.tolerance.is_finite() or args.tolerance < 0:
        parser.error('jobs/attempts must be positive; tolerance must be finite and nonnegative')
    return args


def main():
    args = parse_args()
    invocation = os.environ.get('HW2_RUN_COMMAND') or h.shell_command([sys.executable] + sys.argv)
    h.setup_logging(args.verbose, invocation)
    def interrupt(signum, frame):
        raise KeyboardInterrupt('Signal ' + str(signum))
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    h.LOG.info('Session %s; HW2 root=%s; jobs=%d', SESSION, h.ROOT, args.jobs)
    code = 0
    try:
        dispatch(args)
    except KeyboardInterrupt:
        h.stop_processes()
        h.LOG.error('Interrupted; completed artifacts retained')
        code = 130
    except Exception:
        h.LOG.exception('Workflow failed')
        code = 1
    finally:
        if args.design != 'collect':
            try:
                collect()
            except Exception:
                h.LOG.exception('Result collection failed')
                code = code or 1
    return code


if __name__ == '__main__':
    sys.exit(main())
