#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
systemctl restart strata-sc117
exec ./status.sh --wait
