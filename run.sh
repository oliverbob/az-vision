#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure Python bootstrap is enabled by default so missing deps
# (including diffusers) are auto-installed via run.py.
: "${RUN_PYTHON_BOOTSTRAP:=1}"
export RUN_PYTHON_BOOTSTRAP

exec python3 "${ROOT_DIR}/run.py" "$@"