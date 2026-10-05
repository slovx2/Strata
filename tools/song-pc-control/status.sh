#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec /mnt/data/strata-deploy/Strata/.venv/bin/python ./status.py "$@"
