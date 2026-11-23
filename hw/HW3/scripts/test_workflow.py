"""Exercise automation with fabricated tool evidence, never real EDA claims.

Each test uses a disposable project. The actual RTL sources and published
project results are untouched, including during end-to-end scheduler tests.
"""

import ast
import ctypes
import ctypes.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import helper as h
import workflow as w


class WorkflowTests(unittest.TestCase):
    """Test the coordinator's behavior without requiring VCS or DC licenses."""

    def setUp(self):
        """Build an isolated project and redirect helper paths into it."""
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original_root = h.ROOT
        for folder in ('rtl', 'rtl-sim', 'gate-level-sim', 'syn', 'scripts', 'testdata'):
            shutil.copytree(str(h.ROOT / folder), str(self.root / folder),
                            ignore=shutil.ignore_patterns('*.log', '__pycache__', '*_work', 'work', 'tune'))
        shutil.copyfile(str(h.ROOT / 'run.sh'), str(self.root / 'run.sh'))
        for top in h.TOPS:
            (self.root / 'rtl' / (top + '.v')).write_text('module ' + top + '; endmodule\n')
        h.ROOT = self.root
        h.CANCELLED.clear()
        self.cell = self.root / 'cell.v'
        self.cell.write_text('// mock cell library\n')
        self.environment = patch.dict(os.environ, {'HW3_CELL_MODEL': str(self.cell)})
        self.environment.start()

    def tearDown(self):
        """Restore global paths and delete only this test's disposable project."""
        self.environment.stop()
        h.ROOT = self.original_root
        h.CANCELLED.clear()
        self.temporary.cleanup()

    def fake_tool(self, command, cwd, log, environment=None, append=False, eda=True):
        """Create representative DC/VCS artifacts and command transcripts."""
        cwd, log = Path(cwd), Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a' if append else 'w') as output:
            output.write('$ ' + h.command_text(command, environment) + '\n')
            output.write('HW3_DC_COMPLETE: mock\n' if environment else 'PASS: mock tool\n')

        if environment:
            top = environment['TOP']
            period = float(environment['PERIOD_NS'])
            for extension in ('v', 'ddc', 'sdc', 'sdf'):
                (cwd / (top + '_syn.' + extension)).write_text('mock ' + str(period))
            (cwd / (top + '_units_report.txt')).write_text('Time_unit : 1.0e-09 Second(ns)\n')
            (cwd / (top + '_timing_report.txt')).write_text(
                'data arrival time 2.0\nslack (MET) ' + str(period - 2) + '\n')
            (cwd / (top + '_check_timing.txt')).write_text('Timing coverage checked\n')
            (cwd / (top + '_area_report.txt')).write_text(
                'Combinational area: 100\nNoncombinational area: 20\nTotal cell area: 120\n')
            (cwd / (top + '_power_report.txt')).write_text(
                'Total Dynamic Power = 0.02 mW\nCell Leakage Power = 100 nW\n')
        elif '-o' in command:
            executable = Path(command[command.index('-o') + 1])
            executable.write_text('mock executable')
            # Keep compilation settings available to the fabricated simulator.
            definitions = {part.split('=', 1)[0]: part.split('=', 1)[1]
                           for part in command if part.startswith('+define+')}
            h.save_json(executable.parent / 'mock_settings.json', definitions)
        elif any(str(part).startswith('+RESULT=') for part in command):
            options = dict(part[1:].split('=', 1) for part in command[1:] if '=' in part)
            settings = h.load_json(cwd / 'mock_settings.json')
            latency = int(settings['+define+DUT_LATENCY'])
            count = len(Path(options['VECTORS']).read_text().splitlines())
            resets = 2 if latency else 0
            h.save_json(options['RESULT'], {'status': 'passed', 'vectors': count,
                        'checked': count + resets, 'errors': 0, 'latency': latency,
                        'reset_checks': resets, 'period_ns': float(settings['+define+TB_PERIOD'])})
            Path(options['VCD']).write_text('$mock waveform\n')
            if '+GATE' in command:
                self.assertEqual(settings.get('+define+HW3_GATE_SIM'), '1')
                self.assertEqual(settings.get('+define+HW3_SDF_FILE'),
                                 '"' + options['SDF'] + '"')
                (cwd / 'sdf_annotation.rpt').write_text(
                    'SDF annotation completed:\nTotal errors: 0\nTotal warnings: 0\n')
            else:
                self.assertNotIn('+define+HW3_GATE_SIM', settings)

    def test_full_flow_and_overwrite(self):
        """Run all DAG branches twice and confirm fixed paths and replacement."""
        args = w.parse_args(['all', 'all', '--jobs', '3'])
        with patch.object(h, 'run_command', self.fake_tool), patch.object(h, 'tool', return_value='mock'):
            w.dispatch(args)
            w.collect()
            log = self.root / 'syn/area/DP4/synth.log'
            first = log.read_text()
            log.write_text('OLD LOG MUST DISAPPEAR\n' + first)
            w.dispatch(args)
            self.assertNotIn('OLD LOG', log.read_text())
        self.assertTrue(w.selected('DP4_pipe', 'mid')['converged'])
        self.assertEqual(w.verification_current('DP4_pipe', 'mid', w.selected('DP4_pipe', 'mid')), 'passed')
        self.assertEqual(len(list((self.root / 'syn/area').glob('*/selected.json'))), 3)
        self.assertFalse((self.root / 'result/runs').exists())
        self.assertIn('20.1', (self.root / 'result/summary.csv').read_text())

    def test_tuning_bounds_and_fallback(self):
        """Keep a passing but unconverged fallback and block downstream use."""
        args = w.parse_args(['DP4', 'tune', 'area', '--max-attempts', '1'])
        with patch.object(h, 'run_command', self.fake_tool), patch.object(h, 'tool', return_value='mock'):
            with self.assertRaisesRegex(RuntimeError, 'did not converge'):
                w.tune('DP4', 'area', args)
        self.assertFalse(w.selected('DP4', 'area', converged=False)['converged'])
        with self.assertRaisesRegex(RuntimeError, 'unconverged'):
            w.selected('DP4', 'area')
        search = h.load_json(self.root / 'syn/area/DP4/search.json')
        self.assertEqual(len(search['attempts']), 1)

    def test_trial_history_and_reset(self):
        """Keep every trial and publish an earlier winner before replacing history."""
        args = w.parse_args(['DP4', 'tune', 'area', '--area-seed', '10',
                             '--resolution', '1', '--tolerance', '0'])

        def timing_tool(command, cwd, log, environment=None, **kwargs):
            """Make ten ns pass and nine ns fail to select the earlier trial."""
            self.fake_tool(command, cwd, log, environment, **kwargs)
            period = float(environment['PERIOD_NS'])
            (Path(cwd) / 'DP4_timing_report.txt').write_text(
                'data arrival time 9.5\nslack (MET) ' + str(period - 9.5) + '\n')

        with patch.object(h, 'run_command', timing_tool), patch.object(h, 'tool', return_value='mock'):
            w.tune('DP4', 'area', args)
        directory = h.stage_directory('DP4', 'synth', 'area')
        trials = sorted((directory / 'tune').iterdir())
        self.assertEqual([p.name for p in trials], ['001_period_10ns', '002_period_9ns'])
        self.assertEqual(w.selected('DP4', 'area')['trial'], 'tune/001_period_10ns')
        self.assertEqual((directory / 'DP4_syn.v').read_text(), 'mock 10.0')
        self.assertEqual((directory / 'synth.log').read_text().count('$ '), 2)
        for trial in trials:
            self.assertTrue((trial / 'rtl_files.txt').is_file())
            self.assertTrue((trial / 'synth.log').is_file())
            self.assertIn(h.load_json(trial / 'trial.json')['status'], ('passed', 'timing_violated'))

        other = h.fresh_directory(h.tune_directory('DP4', 'delay')) / 'keep'
        other.write_text('keep')
        args = w.parse_args(['DP4', 'synth', 'area'])
        with patch.object(h, 'run_command', self.fake_tool), patch.object(h, 'tool', return_value='mock'):
            w.single_synthesis('DP4', 'area', args)
        self.assertEqual([p.name for p in (directory / 'tune').iterdir()], ['001_period_20ns'])
        self.assertEqual(other.read_text(), 'keep')
        self.assertEqual(h.trial_directory('DP4', 'area', 2, '8.500').name, '002_period_8.5ns')
        self.assertEqual(h.trial_directory('DP4', 'area', 3, '0.001').name, '003_period_0.001ns')

    def test_failed_trial_is_retained(self):
        """Retain tool failure and interruption evidence without a valid selection."""
        args = w.parse_args(['DP4', 'synth', 'area'])
        for error, status in ((RuntimeError('tool failed'), 'failed'),
                              (KeyboardInterrupt(), 'interrupted')):
            with patch.object(h, 'run_command', side_effect=error), patch.object(h, 'tool', return_value='mock'):
                with self.assertRaises(type(error)):
                    w.single_synthesis('DP4', 'area', args)
            directory = h.stage_directory('DP4', 'synth', 'area')
            trial = directory / 'tune/001_period_20ns'
            self.assertEqual(h.load_json(trial / 'trial.json')['status'], status)
            self.assertIn('FAIL:', (trial / 'synth.log').read_text())
            self.assertEqual(h.load_json(directory / 'search.json')['attempts'][0]['status'], status)
            with self.assertRaises(RuntimeError):
                w.selected('DP4', 'area')

    def test_stale_artifacts_and_sources(self):
        """Reject exports or source files modified after selection."""
        args = w.parse_args(['DP4', 'synth', 'delay'])
        with patch.object(h, 'run_command', self.fake_tool), patch.object(h, 'tool', return_value='mock'):
            w.single_synthesis('DP4', 'delay', args)
        artifact = self.root / 'syn/delay/DP4/DP4_syn.v'
        artifact.write_text('changed')
        with self.assertRaisesRegex(RuntimeError, 'export changed'):
            w.selected('DP4', 'delay')
        (self.root / 'rtl/DP4.v').write_text('changed source')
        with self.assertRaisesRegex(RuntimeError, 'Stale'):
            w.selected('DP4', 'delay')

    def test_failed_dependencies_invalidate_old_pass(self):
        """An upstream failure skips dependents while independent tasks finish."""
        events = []

        def fail():
            """Represent a failed prerequisite."""
            raise RuntimeError('expected failure')

        def success():
            """Record an independent successful task."""
            events.append('success')

        with self.assertRaises(RuntimeError):
            w.run_tasks({'bad': (fail, []), 'dependent': (success, ['bad']),
                         'independent': (success, [])}, 2)
        states = h.load_json(self.root / 'result/tasks.json')
        self.assertEqual(states, {'bad': 'failed', 'dependent': 'skipped', 'independent': 'passed'})
        self.assertEqual(events, ['success'])

        path = w.verification_path('DP4')
        h.save_json(path, {'status': 'passed'})
        args = w.parse_args(['DP4', 'pre'])
        with patch.object(w, 'preparation', side_effect=RuntimeError('reference failed')):
            with self.assertRaises(RuntimeError):
                w.dispatch(args)
        self.assertEqual(h.load_json(path)['status'], 'pending')

    def test_transcripts_completion_and_sdf(self):
        """Merge stderr, overwrite logs, and reject incomplete/invalid evidence."""
        log = self.root / 'probe.log'
        command = [sys.executable, '-B', '-c', 'import sys; print("out"); print("err",file=sys.stderr)']
        h.run_command(command, self.root, log, eda=False)
        text = log.read_text()
        self.assertTrue(text.startswith('$ '))
        self.assertIn('\nout\n', text)
        self.assertIn('\nerr\n', text)
        h.run_command([sys.executable, '-B', '-c', 'print("new")'], self.root, log, eda=False)
        self.assertNotIn('\nout\n', log.read_text())
        h.run_command([sys.executable, '-B', '-c', 'print("appended")'], self.root, log, append=True, eda=False)
        self.assertEqual(log.read_text().count('$ '), 2)

        with self.assertRaises(RuntimeError):
            h.validate_completion({'status': 'passed', 'vectors': 1}, 2048, 4, 20)
        annotation = self.root / 'annotation.rpt'
        annotation.write_text('SDF annotation completed:\nTotal errors: 0\nTotal warnings: 0\n')
        h.validate_sdf(annotation, 'PASS: done')
        with self.assertRaises(RuntimeError):
            h.validate_sdf(annotation, 'Timing violation: setup')
        annotation.write_text('SDF annotation completed:\nTotal errors: 1\nTotal warnings: 0\n')
        with self.assertRaises(RuntimeError):
            h.validate_sdf(annotation, '')

    def test_stage_logs_and_work_paths(self):
        """Preserve unrelated stages/designs and publish only stage-local work."""
        def run(target, action, strategy=None):
            """Exercise the real dispatcher and main lifecycle with mocked EDA."""
            args = w.parse_args([target, action] if target != 'collect' else [target])
            if strategy:
                args.strategy = strategy
            with patch.object(w, 'parse_args', return_value=args), \
                    patch.object(h, 'run_command', self.fake_tool), \
                    patch.object(h, 'tool', return_value='mock'), patch.object(w.signal, 'signal'), \
                    patch.object(sys, 'stdout', io.StringIO()):
                self.assertEqual(w.main(), 0)

        run('DP4', 'pre')
        pre_log = self.root / 'rtl-sim/DP4/run.log'
        first = pre_log.read_text()
        self.assertIn('START DP4/pre', first)
        self.assertIn('DONE DP4/pre', first)
        self.assertTrue((self.root / 'rtl-sim/DP4/work/simv').is_file())
        self.assertTrue((self.root / 'testdata/run.log').is_file())
        run('DP4_pipe', 'pre')
        self.assertEqual(pre_log.read_text(), first)
        pipeline_log = (self.root / 'rtl-sim/DP4_pipe/run.log').read_text()

        pre_log.write_text('OLD RUN\n' + first)
        run('DP4', 'pre')
        self.assertNotIn('OLD RUN', pre_log.read_text())
        self.assertEqual((self.root / 'rtl-sim/DP4_pipe/run.log').read_text(), pipeline_log)
        current = pre_log.read_text()
        run('DP4', 'all')
        for strategy in h.STRATEGIES:
            directory = self.root / 'syn' / strategy / 'DP4'
            self.assertIn('DONE DP4/' + strategy + '/synth', (directory / 'run.log').read_text())
            trial = directory / w.selected('DP4', strategy)['trial']
            self.assertTrue((trial / 'DP4_syn.v').is_file())
            self.assertTrue((trial / 'trial.json').is_file())
            self.assertFalse((directory / 'work').exists())
            gate = self.root / 'gate-level-sim' / strategy / 'DP4'
            self.assertTrue((gate / 'work/sdf_annotation.rpt').is_file())
            self.assertIn('/post', (gate / 'run.log').read_text())

        # A synthesis-only invocation does not overwrite the RTL-stage log.
        current = pre_log.read_text()
        delay_log = self.root / 'syn/delay/DP4/run.log'
        delay = delay_log.read_text()
        gate_log = self.root / 'gate-level-sim/area/DP4/run.log'
        gate = gate_log.read_text()
        run('DP4', 'synth', 'area')
        self.assertEqual(pre_log.read_text(), current)
        self.assertEqual(delay_log.read_text(), delay)
        self.assertEqual(gate_log.read_text(), gate)
        reference = (self.root / 'testdata/run.log').read_text()
        run('collect', '')
        self.assertEqual(pre_log.read_text(), current)
        self.assertEqual((self.root / 'testdata/run.log').read_text(), reference)
        self.assertFalse(list((self.root / 'result').rglob('run.log')))
        self.assertFalse((self.root / 'result/work').exists())

    def test_parallel_stage_output_isolation(self):
        """Route simultaneous command output and stderr to the correct stage."""
        barrier = threading.Barrier(2)
        logs = {key: self.root / (key + '.log') for key in ('alpha', 'beta')}
        for path in logs.values():
            path.write_text('$ test invocation\n')

        def worker(key):
            """Print in fragments and stream an actual child's mixed output."""
            barrier.wait(timeout=5)
            for i in range(10):
                print(key + ' stdout ' + str(i))
                print(key + ' stderr ' + str(i), file=sys.stderr)
            h.run_command([sys.executable, '-B', '-c',
                           'import sys; print("' + key + ' child"); '
                           'print("' + key + ' child error", file=sys.stderr)'],
                          self.root, self.root / (key + '_command.log'), eda=False)

        tasks = {key: (lambda key=key: worker(key), []) for key in logs}
        with patch.object(sys, 'stdout', io.StringIO()):
            w.run_tasks(tasks, 2, logs)
        for key, path in logs.items():
            text = path.read_text()
            other = 'beta' if key == 'alpha' else 'alpha'
            self.assertIn(key + ' child error', text)
            self.assertIn('DONE ' + key, text)
            self.assertNotIn(other, text)

    def test_skipped_stage_log_replaces_old_success(self):
        """Replace stale stage success with an explicit dependency-failure record."""
        path = self.root / 'rtl-sim/DP4/run.log'
        path.parent.mkdir(parents=True)
        path.write_text('DONE DP4/pre\nOLD SUCCESS\n')
        args = w.parse_args(['DP4', 'pre'])
        with patch.object(w, 'preparation', side_effect=RuntimeError('reference failure')):
            with self.assertRaises(RuntimeError):
                w.dispatch(args)
        self.assertIn('SKIP DP4/pre', path.read_text())
        self.assertNotIn('OLD SUCCESS', path.read_text())
        self.assertIn('FAIL reference: reference failure',
                      (self.root / 'testdata/run.log').read_text())

        # Requested synthesis history is reset even when reference checks fail.
        marker = h.fresh_directory(h.tune_directory('DP4', 'area')) / 'old_trial'
        marker.write_text('old evidence')
        args = w.parse_args(['DP4', 'all', 'area'])
        with patch.object(w, 'preparation', side_effect=RuntimeError('reference failure')):
            with self.assertRaises(RuntimeError):
                w.dispatch(args)
        self.assertFalse(marker.exists())
        self.assertEqual(h.load_json(w.selection_path('DP4', 'area'))['status'], 'pending')

    def test_interrupted_stage_logs_and_stream_restoration(self):
        """Record interrupted workers and pending stages, then restore streams."""
        entered = threading.Event()
        logs = {key: self.root / (key + '.log') for key in ('active', 'later')}
        for path in logs.values():
            path.write_text('$ interrupted invocation\nPENDING\n')

        def worker():
            """Start a disposable child that the interrupted scheduler must reap."""
            entered.set()
            h.run_command([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
                          self.root, self.root / 'interrupted_command.log', eda=False)

        def interrupt(*args, **kwargs):
            """Interrupt only after the scheduler starts the first stage."""
            self.assertTrue(entered.wait(timeout=5))
            raise KeyboardInterrupt()

        terminate = h.terminate_processes
        console = io.StringIO()
        with patch.object(sys, 'stdout', console), patch.object(w, 'wait', interrupt), \
                patch.object(h, 'terminate_processes', side_effect=lambda: terminate(force=True)):
            with self.assertRaises(KeyboardInterrupt):
                w.run_tasks({'active': (worker, []), 'later': (worker, ['active'])}, 1, logs)
            self.assertIs(sys.stdout, console)
        self.assertIn('FAIL active:', logs['active'].read_text())
        self.assertIn('SKIP later: invocation interrupted', logs['later'].read_text())
        self.assertEqual(h.load_json(self.root / 'result/tasks.json'),
                         {'active': 'failed', 'later': 'skipped'})
        self.assertFalse(h.PROCESSES)

    def test_safe_cleanup_and_interruption(self):
        """Restrict deletion and verify cancellation prevents new tool launches."""
        with self.assertRaises(RuntimeError):
            h.fresh_directory(self.root / 'rtl')
        path = h.fresh_directory(h.work_directory('DP4', 'pre'))
        (path / 'old').write_text('generated')
        h.fresh_directory(path)
        self.assertFalse((path / 'old').exists())
        for invalid in (path.parent, self.root / 'syn/delay/DP4/work',
                        self.root / 'rtl-sim/UNKNOWN/work', self.root / 'result/work/test'):
            with self.assertRaises(RuntimeError):
                h.fresh_directory(invalid)
        h.terminate_processes(force=True)
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            h.run_command([sys.executable, '-c', 'pass'], self.root, self.root / 'unused.log')

    def test_cleanup_rejects_symlink_redirection(self):
        """Do not follow a generated work symlink into another directory."""
        destination = self.root / 'protected'
        destination.mkdir()
        marker = destination / 'keep.txt'
        marker.write_text('keep')
        path = h.work_directory('DP4', 'pre')
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.symlink_to(destination, target_is_directory=True)
        except OSError as error:
            self.skipTest('Symlinks unavailable: ' + str(error))
        with self.assertRaises(RuntimeError):
            h.fresh_directory(path)
        self.assertEqual(marker.read_text(), 'keep')

    def test_dc_output_path_guard(self):
        """Exercise the actual Tcl validator without loading Design Compiler."""
        source = (self.original_root / 'syn/dc.tcl').read_text()
        procedure = source.split('proc validate_output_path', 1)[1].split('# One DC process', 1)[0]
        procedure = 'proc validate_output_path' + procedure
        valid = h.trial_directory('DP4', 'delay', 1, 20)
        script = procedure + '\nset checked_output [validate_output_path {%s} delay DP4 {%s}]\n' % (
            self.root, valid)
        for invalid in (valid.parent, self.root / 'result/work/DP4/delay/attempt',
                        h.trial_directory('DP4_pipe', 'delay', 1, 20),
                        valid / 'nested', valid.parent / '000_period_10ns',
                        valid.parent / '001_period_0ns', valid.parent / 'attempt'):
            script += 'if {![catch {validate_output_path {%s} delay DP4 {%s}}]} {error {Unsafe path accepted}}\n' % (
                self.root, invalid)
        # Evaluate PrimeTime's actual input-path construction without EDA commands.
        power_source = (self.original_root / 'prime_time/pt.tcl').read_text()
        power_prefix = power_source.split('foreach input_file', 1)[0]
        power_prefix = '\n'.join(line for line in power_prefix.splitlines()
                                 if not line.startswith('set project_root '))
        script += 'set project_root {%s}\nset search_path {}\n' % self.root
        script += 'set ::env(TOP) DP4\nset ::env(SYN_CONFIG) delay\n' + power_prefix + '\n'
        for variable, relative in (
                ('netlist', 'syn/delay/DP4/DP4_syn.v'),
                ('sdc', 'syn/delay/DP4/DP4_syn.sdc'),
                ('sdf', 'syn/delay/DP4/DP4_syn.sdf'),
                ('vcd', 'gate-level-sim/delay/DP4/wave.vcd')):
            script += 'if {$%s ne {%s}} {error {Incorrect PrimeTime input}}\n' % (
                variable, self.root / relative)
        script += 'set checked_output\n'
        output = self.evaluate_tcl(script)
        self.assertIn(str(valid).replace('\\', '/'), output.replace('\\', '/'))

    def evaluate_tcl(self, script):
        """Evaluate actual Tcl using tclsh or the available shared Tcl library."""
        if shutil.which('tclsh'):
            wrapper = 'if {[catch {\n' + script + '\n} output]} {puts stderr $output; exit 1}\nputs $output\n'
            result = subprocess.run(['tclsh'], input=wrapper, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = result.stdout
        else:
            # Ubuntu may supply the Tcl library without the tclsh executable.
            library = ctypes.util.find_library('tcl8.6')
            if not library:
                self.skipTest('Tcl is required for synthesis script checks')
            tcl = ctypes.CDLL(library)
            tcl.Tcl_CreateInterp.restype = ctypes.c_void_p
            tcl.Tcl_Eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            tcl.Tcl_GetStringResult.argtypes = [ctypes.c_void_p]
            tcl.Tcl_GetStringResult.restype = ctypes.c_char_p
            tcl.Tcl_DeleteInterp.argtypes = [ctypes.c_void_p]
            interpreter = tcl.Tcl_CreateInterp()
            try:
                status = tcl.Tcl_Eval(interpreter, script.encode())
                output = tcl.Tcl_GetStringResult(interpreter).decode()
                self.assertEqual(status, 0, output)
            finally:
                tcl.Tcl_DeleteInterp(interpreter)
        return output

    def test_input_and_output_timing_constraints(self):
        """Check launch edges and budgets by evaluating the actual DC constraint block."""
        source = (self.original_root / 'syn/dc.tcl').read_text()
        constraints = source.split('# DP4 uses a virtual clock;', 1)[1]
        constraints = constraints.split('# Reset is inactive', 1)[0]
        constraints = constraints[constraints.index('\n'):]

        # Stub tool collections and capture timing commands without a DC license.
        stubs = '''
proc get_ports {names} {return $names}
proc all_inputs {} {return {clk rst precision x1 y1}}
proc all_outputs {} {return z}
proc remove_from_collection {items removed} {
    set result {}
    foreach item $items {
        if {$item ni $removed} {lappend result $item}
    }
    return $result
}
proc get_clocks {name} {return $name}
proc create_clock {args} {set ::clock_args $args}
proc set_fix_hold {args} {}
proc set_dont_touch_network {args} {}
proc set_ideal_network {args} {}
proc set_input_delay {args} {set ::input_args $args}
proc set_output_delay {args} {set ::output_args $args}
'''
        for top in h.TOPS:
            for period in (10, 20):
                script = stubs + '\nset Top %s\nset period %s\n' % (top, period)
                script += constraints
                script += '''
if {[lindex $input_args 0] != 0} {error {Nonzero input delay}}
if {[lindex $input_args end] ne {precision x1 y1}} {error {Incorrect data inputs}}
if {([lsearch -exact $input_args -clock_fall] >= 0) != ($Top ne "DP4")} {
    error {Incorrect input launch edge}
}
if {[lindex $output_args 0] != $period / 2.0} {error {Incorrect output budget}}
if {[lindex $clock_args end] eq "clk" && $Top eq "DP4"} {
    error {DP4 clock must be virtual}
}
if {[lindex $clock_args end] ne "clk" && $Top ne "DP4"} {
    error {Pipeline clock must use the physical port}
}
set result passed
'''
                self.assertEqual(self.evaluate_tcl(script).strip(), 'passed')

    def test_active_child_is_terminated(self):
        """Cancel an actual sleeping Python child and ensure the worker reaps it."""
        errors = []

        def worker():
            """Run a disposable process and retain its expected cancellation error."""
            try:
                h.run_command([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
                              self.root, self.root / 'child.log', eda=False)
            except RuntimeError as error:
                errors.append(str(error))

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic() + 5
        while not h.PROCESSES and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(h.PROCESSES)
        h.terminate_processes(force=True)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(h.PROCESSES)
        self.assertTrue(errors)

    def test_readability_contract(self):
        """Require a docstring on every Python module, function, method, and class."""
        for directory in ('scripts', 'testdata'):
            for path in (self.original_root / directory).glob('*.py'):
                tree = ast.parse(path.read_text())
                self.assertTrue(ast.get_docstring(tree), str(path))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        self.assertTrue(ast.get_docstring(node), '{}:{}'.format(path.name, node.name))


if __name__ == '__main__':
    # Silence expected mock progress while unittest retains its own result output.
    unittest.main()
