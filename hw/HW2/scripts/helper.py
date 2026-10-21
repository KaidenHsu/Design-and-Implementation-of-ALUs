"""Small shared utilities for the HW2 course-server workflow (Python 3.6+)."""

import hashlib
import json
import logging
import shlex
import math
import os
import re
import signal
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGNS = {
    'FXP_FMA': ('FXP_FMA', 'TB_FXP_FMA.v', 1, 200),
    'FLP_FMA': ('FMA', 'TB_FMA.v', 1, 215),
    'FLP_FMA_4': ('FMA_pipe', 'TB_FMA_pipe.v', 4, 215),
}
OPTS = ('Area', 'Delay', 'Between')
CELL_MODEL = '/cad/CBDK/ADFP/Executable_Package/Collaterals/IP/stdcell/N16ADFP_StdCell/VERILOG/N16ADFP_StdCell.v'
LOG = logging.getLogger('hw2')
PROCESSES = set()
PROCESS_LOCK = threading.Lock()
CANCELLED = threading.Event()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, data):
    """Atomic state writes avoid partial JSON during parallel result collection."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    os.replace(str(temporary), str(path))


def load_json(path):
    return json.loads(Path(path).read_text())


def rtl_files(design):
    directory = ROOT / design / 'rtl'
    manifest = directory / 'files.txt'
    if manifest.exists():
        files = [(directory / line.strip()).resolve()
                 for line in manifest.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        require(all(directory.resolve() in f.parents and f.suffix == '.v'
                    for f in files), 'files.txt must list Verilog files inside rtl/')
    else:
        files = sorted(directory.rglob('*.v'))
    require(files and all(f.is_file() for f in files), 'No valid RTL for ' + design)
    require(len(set(files)) == len(files), 'Duplicate RTL files in ' + design)
    return files


def fingerprint(design):
    """One small source hash prevents post-sim from using an old RTL selection."""
    directory = ROOT / design
    files = rtl_files(design) + sorted((directory / 'rtl').rglob('*.vh'))
    files += [directory / 'compile' / 'dc.tcl']
    for optional in ('rtl/files.txt', 'compile/stage_paths.tcl'):
        if (directory / optional).exists():
            files.append(directory / optional)
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    digest.update(os.environ.get('HW2_LIBRARY_ROOT', 'TA_DEFAULT').encode())
    return digest.hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def simulation_inputs(design, directory, suite="all"):
    """A compact hash covers the testbench and the input/golden files it uses."""
    directory = Path(directory)
    files = [directory / DESIGNS[design][1]]
    suites = ('normal',) if design == 'FXP_FMA' or suite == 'normal' else ('normal', 'special')
    names = ('a.txt', 'b.txt', 'c.txt') + (() if design == 'FXP_FMA' else ('ab+c.txt',))
    files += [directory / suite / name for suite in suites for name in names]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def setup_logging(verbose, command=None):
    """Keep coordinator messages in result/run.log and tool output beside each run."""
    (ROOT / 'result').mkdir(exist_ok=True)
    LOG.setLevel(logging.INFO)
    LOG.handlers.clear()
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%dT%H:%M:%S')
    master = logging.FileHandler(str(ROOT / 'result' / 'run.log'), encoding='utf-8')
    master.setFormatter(formatter)
    master.addFilter(lambda record: not getattr(record, 'tool_output', False))
    LOG.addHandler(master)
    if command:
        master.stream.write('$ ' + command + '\n')
        master.flush()
    terminal = logging.StreamHandler()
    terminal.setFormatter(formatter)
    terminal.addFilter(lambda record: verbose or not getattr(record, 'tool_output', False)
                       or record.levelno >= logging.WARNING)
    LOG.addHandler(terminal)


def stop_processes():
    CANCELLED.set()
    with PROCESS_LOCK:
        for process in list(PROCESSES):
            if process.poll() is None:
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except OSError:
                    pass
    # A tool ignoring TERM must not keep the coordinator waiting indefinitely.
    def force_stop():
        with PROCESS_LOCK:
            for process in list(PROCESSES):
                if process.poll() is None:
                    try:
                        if os.name == 'posix':
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    except OSError:
                        pass
    timer = threading.Timer(5, force_stop)
    timer.daemon = True
    timer.start()


def shell_command(command, environment=None):
    """Display a runnable POSIX command, including task-specific environment."""
    prefix = ['env'] + ['{}={}'.format(key, value)
                        for key, value in sorted(environment.items())] if environment else []
    return ' '.join(shlex.quote(str(arg)) for arg in prefix + list(command))


def csv_row(row):
    """Format measured numbers only; retain integer stages and boolean statuses."""
    return {key: (format(value, '.3f') if isinstance(value, float) else value)
            for key, value in row.items()}


def run_command(command, cwd, log, environment=None):
    """Argument lists, per-task logs, and locked logging keep parallel runs clear."""
    require(not CANCELLED.is_set(), 'Session interrupted')
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Python is launched without the EDA loader path; restore it only for tools.
    if env.pop('HW2_EDA_LD_LIBRARY_PATH_SET', '0') == '1':
        env['LD_LIBRARY_PATH'] = env.pop('HW2_EDA_LD_LIBRARY_PATH', '')
    env.update(environment or {})
    label = str(log.relative_to(ROOT))
    display_command = shell_command(command, environment)
    LOG.info('[%s] cwd=%s\n$ %s', label, cwd, display_command)
    with log.open('w', encoding='utf-8') as sink:
        sink.write('$ ' + display_command + '\n')
        sink.flush()
        with PROCESS_LOCK:
            require(not CANCELLED.is_set(), 'Session interrupted')
            process = subprocess.Popen(command, cwd=str(cwd), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, errors='replace',
                start_new_session=(os.name == 'posix'))
            PROCESSES.add(process)
        try:
            for line in process.stdout:
                sink.write(line)
                sink.flush()
                # Classify each line independently; DC also echoes Tcl source.
                diagnostic_level = logging.INFO
                if re.search(r'(?i)^\s*(?:error|fatal)(?:-|:)|^\s*(?:FAIL|OVERALL FAIL)\b|^\s*HW2_DC_ERROR:|^\s*timing violation', line):
                    diagnostic_level = logging.ERROR
                elif re.search(r'(?i)^\s*warning(?:-|:)', line):
                    diagnostic_level = logging.WARNING
                LOG.log(diagnostic_level, '[%s] %s', label, line.rstrip(), extra={'tool_output': True})
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
    require(code == 0, 'Tool exited {}; see {}'.format(code, log))
    require(not CANCELLED.is_set(), 'Session interrupted')


def time_scale(directory):
    text = (Path(directory) / 'units_report.txt').read_text()
    match = re.search(r'Time(?:_|\s+)units?\s*:\s*([\d.eE+-]+)\s*([A-Za-z]+)', text, re.I)
    require(match, 'Missing library time unit in ' + str(directory))
    scales = {'s': 1e9, 'second': 1e9, 'seconds': 1e9, 'ms': 1e6,
              'us': 1e3, 'ns': 1, 'ps': 1e-3, 'fs': 1e-6}
    require(match.group(2).lower() in scales, 'Unsupported library time unit')
    scale = float(match.group(1)) * scales[match.group(2).lower()]
    require(math.isfinite(scale) and scale > 0, 'Invalid library time unit')
    return scale


def timing(path, scale):
    text = Path(path).read_text()
    arrivals = re.findall(r'^\s*data arrival time\s+([\d.eE+-]+)\s*$', text, re.M)
    # Ignore the negative repetition used in the slack subtraction.
    arrivals = [float(value) for value in arrivals if float(value) >= 0]
    slacks = re.findall(r'^\s*slack\s*\([^)]*\)\s+([\d.eE+-]+)\s*$', text, re.M)
    require(arrivals and slacks, 'Missing timing arrival/slack in ' + str(path))
    delay, slack = max(arrivals) * scale, min(map(float, slacks)) * scale
    require(math.isfinite(delay) and math.isfinite(slack), 'Nonfinite timing report')
    return {'delay_ns': delay, 'slack_ns': slack}


def metrics(directory, period):
    directory = Path(directory)
    values = timing(directory / 'timing_report.txt', time_scale(directory))
    coverage = (directory / 'check_timing.txt').read_text()
    require(not re.search(r'(?i)(?:warning|error)[^\n]*(?:unconstrained|not constrained|no clock)', coverage),
            'Unconstrained timing paths; inspect check_timing.txt')
    area = (directory / 'area_report.txt').read_text()
    for key, label in [('comb_um2', 'Combinational area'),
                       ('seq_um2', 'Noncombinational area'), ('total_um2', 'Total cell area')]:
        match = re.search(label + r'\s*:\s*([\d.eE+-]+)', area)
        require(match, 'Missing area field: ' + label)
        values[key] = float(match.group(1))
    power = (directory / 'power_report.txt').read_text()
    for key, label in [('dynamic_w', 'Total Dynamic Power'), ('leakage_w', 'Cell Leakage Power')]:
        match = re.search(label + r'\s*=\s*([\d.eE+-]+)\s*([munp]?W)', power)
        values[key] = (float(match.group(1)) *
                       {'W': 1, 'mW': 1e-3, 'uW': 1e-6, 'nW': 1e-9, 'pW': 1e-12}[match.group(2)]
                       if match else None)
    values['power_w'] = (values['dynamic_w'] + values['leakage_w']
                         if None not in (values['dynamic_w'], values['leakage_w']) else None)
    values['period_ns'] = float(period)
    return values


def validate_sdf(annotation, simulation_log):
    """Do not mistake requested annotation, or a functional pass, for timing success."""
    text = Path(annotation).read_text(errors='replace')
    require('SDF annotation completed:' in text, 'SDF annotation did not complete')
    errors = re.findall(r'Total errors:\s*(\d+)', text)
    require(errors and all(int(n) == 0 for n in errors), 'SDF annotation errors')
    blocks = re.findall(r'(?ms)^\s*Warning-\[(SDFCOM_[A-Z0-9_]+)\](.*?)(?=^\s*(?:Warning-|Error-|Total (?:warnings|errors):)|\Z)', text)
    for code, body in blocks:
        require(code in ('SDFCOM_SWC', 'SDFCOM_IWSBA') and
                re.search(r'delay\s+will\s+still\s+be\s+annotated\.', body, re.I),
                'Unaccepted SDF warning: ' + code)
    warnings = re.findall(r'Total warnings:\s*(\d+)', text)
    require(warnings and int(warnings[-1]) == len(blocks), 'Unclassified SDF warnings')
    codes = re.findall(r'SDFCOM_[A-Z0-9_]+', simulation_log)
    require(all(code in ('SDFCOM_SWC', 'SDFCOM_IWSBA') for code in codes),
            'Unaccepted SDF diagnostic in simulation log')
    require(not re.search(r'(?im)^\s*(?:Error|Fatal)(?:-|:).*SDF|^\s*SDF\s+(?:Error|Fatal)',
                          text + '\n' + simulation_log), 'SDF error/fatal diagnostic')
    require(not re.search(r'(?i)timing violation|\$(?:setup|hold|recovery|removal|setuphold|recrem|width|period)\(',
                          text + '\n' + simulation_log), 'Timing violation/check diagnostic')
    return len(blocks)
