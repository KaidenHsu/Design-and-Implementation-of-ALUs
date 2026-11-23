"""Shared process, provenance, report, and validation helpers for HW3.

Tool transcripts contain only a reproducible command header and merged output.
Destructive cleanup is limited to allowlisted, stage-local generated work paths.
"""

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOPS = {'DP4': 0, 'DP4_pipe': 4, 'DP4_CG': 4}
STRATEGIES = ('area', 'mid', 'delay')
CELL_MODEL = '/cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/VERILOG/N16ADFP_StdCell.v'
PROCESSES = set()
PROCESS_LOCK = threading.Lock()
CANCELLED = threading.Event()


def require(condition, message):
    """Reject invalid inputs or incomplete evidence with a readable error."""
    if not condition:
        raise RuntimeError(message)


def save_json(path, data):
    """Atomically replace current state so readers never see half-written JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    os.replace(str(temporary), str(path))


def load_json(path):
    """Read an existing structured result; absence is not a successful result."""
    return json.loads(Path(path).read_text())


def file_hash(path):
    """Fingerprint exact file contents, including changes to generated artifacts."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def hash_files(paths, settings=None):
    """Hash named files and settings deterministically, independent of timestamps."""
    digest = hashlib.sha256(json.dumps(settings or {}, sort_keys=True).encode())

    for path in sorted(set(map(Path, paths))):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())

    return digest.hexdigest()


def rtl_files(top):
    """Find the chosen top and shared Verilog helpers, or honor rtl/files.txt."""
    directory = ROOT / 'rtl'
    manifest = directory / 'files.txt'

    if manifest.exists():
        names = [line.strip() for line in manifest.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        paths = [(directory / name).resolve() for name in names]
    else:
        # Exclude the other two top files; analyze shared helper modules too.
        paths = [directory / (top + '.v')]
        paths += sorted(p for p in directory.rglob('*.v') if p.stem not in TOPS)

    require(paths and len(paths) == len(set(paths)), 'Empty or duplicate RTL list')
    for path in paths:
        require(directory.resolve() in path.parents and path.suffix == '.v'
                and path.is_file(), 'Invalid RTL source: ' + str(path))

    return paths


def implemented(top):
    """Reject known interface skeletons before consuming server tool licenses."""
    paths = rtl_files(top)
    require(all('Output intentionally undriven' not in p.read_text() for p in paths),
            top + ' is an unimplemented RTL skeleton')
    return paths


def source_hash(top):
    """Cover RTL, include files, synthesis scripts, runner, and library settings."""
    files = rtl_files(top) + list((ROOT / 'rtl').rglob('*.vh'))
    files += [ROOT / 'syn/dc.tcl', ROOT / 'syn/.synopsys_dc.setup', ROOT / 'run.sh']
    files += sorted((ROOT / 'scripts').glob('*.py'))
    if (ROOT / 'rtl/files.txt').exists():
        files.append(ROOT / 'rtl/files.txt')

    return hash_files(files, {'CELL_LIB_DIR': os.environ.get('CELL_LIB_DIR', 'course default')})


def input_hash(post=False):
    """Cover the actual bench, supplied operands, generated answers, and oracle."""
    bench = ROOT / ('gate-level-sim' if post else 'rtl-sim') / 'TB.v'
    return hash_files([bench] + sorted((ROOT / 'testdata').glob('*.py'))
                      + sorted((ROOT / 'testdata').glob('*.txt')))


def stage_directory(top, stage, strategy=None):
    """Locate the design directory containing this stage's logs and outputs."""
    require(top in TOPS, 'Invalid design: ' + str(top))
    if stage == 'pre':
        require(strategy is None, 'RTL simulation has no synthesis strategy')
        return ROOT / 'rtl-sim' / top
    require(strategy in STRATEGIES, 'Invalid strategy: ' + str(strategy))
    if stage == 'synth':
        return ROOT / 'syn' / strategy / top
    require(stage == 'post', 'Invalid stage: ' + str(stage))
    return ROOT / 'gate-level-sim' / strategy / top


def work_directory(top, stage, strategy=None):
    """Return one simulation's disposable build directory."""
    require(stage in ('pre', 'post'), 'Synthesis uses numbered tune trials')
    return stage_directory(top, stage, strategy) / 'work'


def tune_directory(top, strategy):
    """Return the history root replaced at the start of a synthesis stage."""
    return stage_directory(top, 'synth', strategy) / 'tune'


def trial_directory(top, strategy, index, period):
    """Name trials in execution order, preserving meaningful decimal precision."""
    period = Decimal(str(period))
    require(isinstance(index, int) and index > 0, 'Invalid trial index')
    require(period.is_finite() and period > 0, 'Invalid trial period')
    text = format(period, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return tune_directory(top, strategy) / ('{:03d}_period_{}ns'.format(index, text))


def fresh_directory(path):
    """Replace only exact allowlisted work paths, rejecting symlink redirection."""
    requested = Path(os.path.abspath(str(path)))
    path = requested.resolve()
    allowed = set()
    for top in TOPS:
        allowed.add(work_directory(top, 'pre'))
        for strategy in STRATEGIES:
            allowed.add(work_directory(top, 'post', strategy))
            allowed.add(tune_directory(top, strategy))
    allowed = {Path(os.path.abspath(str(item))) for item in allowed}
    require(requested == path and path in allowed,
            'Refusing cleanup outside approved stage work paths: ' + str(requested))

    if path.exists():
        shutil.rmtree(str(path))
    path.mkdir(parents=True)
    return path


def command_text(command, environment=None):
    """Render an executable POSIX command, including task-specific assignments."""
    prefix = ['env'] + ['{}={}'.format(k, v) for k, v in sorted(environment.items())] if environment else []
    return ' '.join(shlex.quote(str(part)) for part in prefix + list(command))


class StageOutput:
    """Mirror terminal output to the calling thread's active stage transcript."""

    def __init__(self):
        """Retain the terminal and allocate independent worker-local sinks."""
        self.console = sys.stdout
        self.lock = threading.RLock()
        self.local = threading.local()

    @contextmanager
    def capture(self, path):
        """Append this thread's writes to its stage log until the context exits."""
        previous = getattr(self.local, 'sink', None)
        sink = Path(path).open('a', encoding='utf-8') if path else None
        self.local.sink = sink
        try:
            yield
        finally:
            self.local.sink = previous
            if sink:
                sink.close()

    def write(self, text):
        """Serialize console writes and route each chunk only to its own stage."""
        with self.lock:
            self.console.write(text)
            sink = getattr(self.local, 'sink', None)
            if sink:
                sink.write(text)
            self.flush()
        return len(text)

    def flush(self):
        """Make progress visible promptly in terminal and transcript."""
        with self.lock:
            self.console.flush()
            sink = getattr(self.local, 'sink', None)
            if sink:
                sink.flush()


@contextmanager
def stage_output():
    """Install one thread-aware router for a scheduler run, then restore streams."""
    stdout, stderr = sys.stdout, sys.stderr
    router = StageOutput()
    sys.stdout = sys.stderr = router
    try:
        yield router
    finally:
        sys.stdout, sys.stderr = stdout, stderr


def terminate_processes(force=False):
    """Cancel tool process groups; escalate after five seconds if necessary."""
    CANCELLED.set()

    with PROCESS_LOCK:
        for process in list(PROCESSES):
            if process.poll() is not None:
                continue
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
                elif force:
                    process.kill()
                else:
                    process.terminate()
            except OSError:
                pass  # The process may have exited between poll and signal.

    if not force:
        timer = threading.Timer(5, terminate_processes, kwargs={'force': True})
        timer.daemon = True
        timer.start()


def run_command(command, cwd, log, environment=None, append=False, eda=True):
    """Run a tool, separating command/output blocks with three blank lines."""
    require(not CANCELLED.is_set(), 'Workflow interrupted')
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({key: str(value) for key, value in (environment or {}).items()})
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    if eda and 'HW3_EDA_LD_LIBRARY_PATH' in env:
        env['LD_LIBRARY_PATH'] = env['HW3_EDA_LD_LIBRARY_PATH']

    header = '$ ' + command_text(command, environment) + '\n'
    print(header, end='', flush=True)

    with log.open('a' if append else 'w', encoding='utf-8') as sink:
        sink.write(header)
        sink.flush()

        with PROCESS_LOCK:
            require(not CANCELLED.is_set(), 'Workflow interrupted')
            process = subprocess.Popen(list(map(str, command)), cwd=str(cwd), env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       universal_newlines=True, errors='replace',
                                       start_new_session=(os.name == 'posix'))
            PROCESSES.add(process)

        output_ends_with_newline = True
        try:
            for line in process.stdout:
                sink.write(line)
                sink.flush()
                print(line, end='', flush=True)
                output_ends_with_newline = line.endswith('\n')
            code = process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
            with PROCESS_LOCK:
                PROCESSES.discard(process)

            # Finish an unterminated output line before adding three empty lines.
            separator = ('\n' if not output_ends_with_newline else '') + '\n' * 3
            sink.write(separator)
            sink.flush()
            print(separator, end='', flush=True)

    require(code == 0, 'Command exited {}: {}'.format(code, log))
    require(not CANCELLED.is_set(), 'Workflow interrupted')


def tool(name):
    """Resolve a server executable, or explain which environment is missing."""
    executable = shutil.which(name)
    require(executable, 'Missing {}; initialize the course EDA environment'.format(name))
    return executable


def time_scale(text):
    """Convert one reported library time unit to ns without double conversion."""
    match = re.search(r'Time(?:_|\s+)units?\s*:\s*([\d.eE+-]+)\s*([A-Za-z]+)', text, re.I)
    require(match, 'Missing library time unit')
    scales = {'s': 1e9, 'second': 1e9, 'seconds': 1e9, 'ms': 1e6,
              'us': 1e3, 'ns': 1, 'ps': 1e-3, 'fs': 1e-6}
    require(match.group(2).lower() in scales, 'Unsupported time unit')
    result = float(match.group(1)) * scales[match.group(2).lower()]
    require(math.isfinite(result) and result > 0, 'Invalid time unit')
    return result


def metrics(directory, top, period):
    """Parse DC evidence into ns, square micrometers, and microwatts."""
    directory = Path(directory)
    scale = time_scale((directory / (top + '_units_report.txt')).read_text())
    timing = (directory / (top + '_timing_report.txt')).read_text()
    arrivals = [float(x) for x in re.findall(r'^\s*data arrival time\s+([\d.eE+-]+)\s*$', timing, re.M)]
    arrivals = [x for x in arrivals if x >= 0]
    slacks = [float(x) for x in re.findall(r'^\s*slack\s*\([^)]*\)\s+([\d.eE+-]+)\s*$', timing, re.M)]
    require(arrivals and slacks, 'Missing timing arrival/slack')
    coverage = (directory / (top + '_check_timing.txt')).read_text()
    require(not re.search(r'(?i)(?:warning|error)[^\n]*(?:unconstrained|not constrained|no clock|no input delay|no output delay)', coverage),
            'Unconstrained timing paths; inspect check_timing report')
    result = {'period_ns': float(period), 'delay_ns': max(arrivals) * scale,
              'slack_ns': min(slacks) * scale}

    area = (directory / (top + '_area_report.txt')).read_text()
    for key, label in [('comb_um2', 'Combinational area'), ('seq_um2', 'Noncombinational area'),
                       ('total_um2', 'Total cell area')]:
        match = re.search(label + r'\s*:\s*([\d.eE+-]+)', area)
        require(match, 'Missing area field: ' + label)
        result[key] = float(match.group(1))

    power = (directory / (top + '_power_report.txt')).read_text()
    for key, label in [('dynamic_uw', 'Total Dynamic Power'), ('leakage_uw', 'Cell Leakage Power')]:
        match = re.search(label + r'\s*=\s*([\d.eE+-]+)\s*([munp]?W)', power)
        result[key] = (float(match.group(1)) * {'W': 1e6, 'mW': 1e3, 'uW': 1, 'nW': 1e-3, 'pW': 1e-6}[match.group(2)]
                       if match else None)
    result['power_uw'] = (result['dynamic_uw'] + result['leakage_uw']
                          if None not in (result['dynamic_uw'], result['leakage_uw']) else None)
    require(all(value is None or math.isfinite(value) for value in result.values()), 'Nonfinite DC metric')
    return result


def validate_sdf(report, simulation_text):
    """Require actual annotation completion, classified warnings, and clean timing."""
    text = Path(report).read_text(errors='replace')
    require('SDF annotation completed:' in text, 'SDF annotation did not complete')
    errors = re.findall(r'Total errors:\s*(\d+)', text)
    require(errors and all(int(n) == 0 for n in errors), 'SDF annotation errors')

    # Only the two HW2 connectivity warnings explicitly confirming annotation
    # are accepted; all unknown warning classes require investigation.
    blocks = re.findall(r'(?ms)^\s*Warning-\[(SDFCOM_[A-Z0-9_]+)\](.*?)(?=^\s*(?:Warning-|Error-|Total (?:warnings|errors):)|\Z)', text)
    for code, body in blocks:
        require(code in ('SDFCOM_SWC', 'SDFCOM_IWSBA') and
                re.search(r'delay\s+will\s+still\s+be\s+annotated\.', body, re.I),
                'Unaccepted SDF warning: ' + code)
    warnings = re.findall(r'Total warnings:\s*(\d+)', text)
    require(warnings and int(warnings[-1]) == len(blocks), 'Unclassified SDF warnings')
    combined = text + '\n' + simulation_text
    require(not re.search(r'(?i)timing violation|\$(?:setup|hold|recovery|removal|setuphold|recrem|width|period)\(', combined),
            'Timing check violation in gate simulation')
    require(not re.search(r'(?im)^\s*(?:Error|Fatal)(?:-|:)', combined), 'Gate simulation error/fatal')
    codes = re.findall(r'SDFCOM_[A-Z0-9_]+', simulation_text)
    require(all(c in ('SDFCOM_SWC', 'SDFCOM_IWSBA') for c in codes), 'Unaccepted simulator SDF diagnostic')


def validate_completion(record, expected, latency, period):
    """Require every vector and scheduled reset check, rather than a PASS alone."""
    resets = (1 + int(expected >= 1000)) if latency else 0
    require(record.get('status') == 'passed' and record.get('vectors') == expected
            and record.get('checked') == expected + resets and record.get('errors') == 0
            and record.get('latency') == latency and record.get('reset_checks') == resets,
            'Incomplete or failed testbench result')
    require(math.isclose(float(record.get('period_ns', -1)), float(period), abs_tol=1e-6),
            'Testbench period does not match requested period')
