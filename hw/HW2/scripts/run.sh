#!/usr/bin/env bash
# Course-server launcher; Python owns scheduling and readable terminal logging.
set -euo pipefail

HW2_SCRIPT_DIR="$(dirname -- "$0")"
HW2_ROOT="$HW2_SCRIPT_DIR/.."
export HW2_ROOT
printf -v HW2_RUN_COMMAND '%q ' bash "$0" "$@"
export HW2_RUN_COMMAND

if [ "$#" -eq 0 ] || [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'USAGE'
Usage: bash scripts/run.sh DESIGN {pre|synth|tune|post|all} [Area|Delay|Between|all] [options]
       bash scripts/run.sh all all --jobs 4
       bash scripts/run.sh collect

Targets: FXP_FMA, FLP_FMA, FLP_FMA_4, all (implemented RTL only).
Options: --jobs N --period NS --cycle NS --wave vcd|fsdb --suite all|normal --verbose
         --area-seed NS --delay-seed NS --resolution NS --tolerance NS
         --max-attempts N --max-period NS
Tool logs: beside pre_sim, post_sim/<strategy>, and compile/<strategy> runs.
Workflow status: result/session.json; coordinator log: result/run.log.
APR is separate and is not run by this launcher.
USAGE
    exit 0
fi

for command in python3 flock; do
    command -v "$command" >/dev/null || { echo "ERROR: missing $command" >&2; exit 1; }
done
mkdir -p "$HW2_ROOT/result"
exec 9>"$HW2_ROOT/result/run.lock"
flock -n 9 || { echo 'ERROR: another HW2 workflow is running' >&2; exit 1; }

# Avoid EDA shared libraries interfering with Python; restore them for tool children.
if [ "${LD_LIBRARY_PATH+x}" = "x" ]; then
    export HW2_EDA_LD_LIBRARY_PATH_SET=1
    export HW2_EDA_LD_LIBRARY_PATH="$LD_LIBRARY_PATH"
else
    export HW2_EDA_LD_LIBRARY_PATH_SET=0
fi
unset LD_LIBRARY_PATH

python3 -u "$HW2_ROOT/scripts/workflow.py" "$@" &
worker=$!
trap 'kill -TERM "$worker" 2>/dev/null || true' INT TERM
set +e
wait "$worker"
status=$?
# wait can return immediately after a trapped signal; keep the lock until cleanup.
if kill -0 "$worker" 2>/dev/null; then
    wait "$worker"
    status=$?
fi
exit "$status"
