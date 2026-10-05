#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
strata_log_path="$(/mnt/data/strata-deploy/Strata/.venv/bin/python -c 'import json; print(json.load(open("config.json"))["log"])')"
if [[ "${1:-}" == "-f" ]]; then
  exec tail -n 80 -F -- "$strata_log_path"
fi
exec tail -n "${1:-80}" -- "$strata_log_path"
