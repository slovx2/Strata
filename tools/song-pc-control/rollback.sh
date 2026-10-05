#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
was_active=0
if systemctl is-active --quiet strata-sc117; then was_active=1; systemctl stop strata-sc117; fi
install -m 600 configs/baseline.json config.json
printf '已切回 256K 原基线配置。\n'
if (( was_active )); then exec ./start.sh; fi
