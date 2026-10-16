#!/bin/bash
# PM2 entrypoint: long-running LF auto-submit (3/day, 1h gap).
set -euo pipefail
SN89_REPO="${SN89_REPO:-/workspace/InfiniteQuant-Subnet}"
SN89_DATA_ROOT="${SN89_DATA_ROOT:-/workspace/sn89_data}"
SN89_PY="${SN89_PY:-/venv/sn89/bin/python}"
cd "$SN89_REPO"
set -a
# shellcheck disable=SC1091
source "$SN89_DATA_ROOT/miner.env"
# shellcheck disable=SC1091
[[ -f "$SN89_DATA_ROOT/fleet_hotkeys.env" ]] && source "$SN89_DATA_ROOT/fleet_hotkeys.env"
set +a
export PYTHONPATH="$SN89_REPO"
export SN89_DATA_ROOT
export SN89_STATE_DIR="${SN89_STATE_DIR:-$HOME/.sn89}"
exec "$SN89_PY" scripts/lf_auto_loop.py \
  --wallet.name "${SN89_WALLET_NAME:-rabi}" \
  --wallet.hotkey "${SN89_WALLET_HOTKEY:-rabi-h1}" \
  --direction "${SN89_AUTO_DIRECTION:-AUTO}" \
  --pick-rank "${SN89_AUTO_PICK_RANK:-0}" \
  "$@"
