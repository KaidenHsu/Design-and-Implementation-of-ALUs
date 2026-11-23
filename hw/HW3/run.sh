#!/usr/bin/env bash
# Run from any directory: paths are anchored to this launcher.
set -euo pipefail

HW3_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export HW3_ROOT
printf -v HW3_RUN_COMMAND '%q ' bash "$0" "$@"
export HW3_RUN_COMMAND

if [[ ${1:-} == --help || ${1:-} == -h || $# == 0 ]]; then
    exec python3 -B "$HW3_ROOT/scripts/workflow.py" --help
fi

# Serialize whole invocations; --jobs controls parallel tasks within one run.
command -v flock >/dev/null || { echo 'ERROR: flock is required' >&2; exit 1; }
mkdir -p "$HW3_ROOT/result"
exec 9>"$HW3_ROOT/result/run.lock"
flock -n 9 || { echo 'ERROR: another HW3 workflow is running' >&2; exit 1; }

# EDA loader paths can conflict with Python. Restore them only for tool children.
if [[ ${LD_LIBRARY_PATH+x} ]]; then
    export HW3_EDA_LD_LIBRARY_PATH="$LD_LIBRARY_PATH"
    unset LD_LIBRARY_PATH
fi

# exec preserves the lock and delivers signals directly to the Python coordinator.
exec python3 -B -u "$HW3_ROOT/scripts/workflow.py" "$@"
