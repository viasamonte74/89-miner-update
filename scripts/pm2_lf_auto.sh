#!/bin/bash
# PM2 entrypoint: long-running LF auto-submit (3/day, 1h gap).
set -euo pipefail
cd /workspace/InfiniteQuant-Subnet
set -a
# shellcheck disable=SC1091
source /workspace/sn89_data/miner.env
# shellcheck disable=SC1091
[[ -f /workspace/sn89_data/fleet_hotkeys.env ]] && source /workspace/sn89_data/fleet_hotkeys.env
set +a
export PYTHONPATH=/workspace/InfiniteQuant-Subnet
export SN89_DATA_ROOT=/workspace/sn89_data
exec /venv/sn89/bin/python scripts/lf_auto_loop.py \
  --wallet.name "${SN89_WALLET_NAME:-rabi}" \
  --wallet.hotkey "${SN89_WALLET_HOTKEY:-rabi-h1}" \
  --direction "${SN89_AUTO_DIRECTION:-AUTO}" \
  --pick-rank "${SN89_AUTO_PICK_RANK:-0}" \
  "$@"
