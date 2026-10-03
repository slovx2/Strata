#!/usr/bin/env bash
set -euo pipefail
# The checked-in fixtures and script are the benchmark contract. Runtime output
# and credentials stay on the deployment's data disk, outside the repository.
STRATA_DEPLOY_ROOT=${STRATA_DEPLOY_ROOT:-/mnt/data/strata-deploy}
STRATA_SOURCE_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
STRATA_RESULT_ROOT="$STRATA_DEPLOY_ROOT/bench-32k-20261003/results/quick"
mkdir -p "$STRATA_RESULT_ROOT"
STRATA_RESULT_FILE="$STRATA_RESULT_ROOT/$(date -u +%Y%m%dT%H%M%S)-$$.json"
exec flock -n "$STRATA_RESULT_ROOT/benchmark.lock" \
  "$STRATA_DEPLOY_ROOT/direct-env.sh" \
  "$STRATA_DEPLOY_ROOT/Strata/.venv/bin/python" \
  "$STRATA_SOURCE_ROOT/tools/bench_single_turn.py" \
  --key-file "$STRATA_DEPLOY_ROOT/api-key" \
  --fixtures "$STRATA_SOURCE_ROOT/bench/song-pc-32k-fixtures/prompts.json" \
  --out "$STRATA_RESULT_FILE" "$@"
