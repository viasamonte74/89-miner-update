#!/bin/bash
# PM2 entrypoint: long-running HF auto-submit (concurrent with LF on same hotkey).
set -euo pipefail
SN89_REPO="${SN89_REPO:-/workspace/InfiniteQuant-Subnet}"
SN89_DATA_ROOT="${SN89_DATA_ROOT:-/workspace/sn89_data}"
SN89_PY="${SN89_PY:-/venv/sn89/bin/python}"
cd "$SN89_REPO"
set -a
# shellcheck disable=SC1091
source "$SN89_DATA_ROOT/miner.env"
set +a
export PYTHONPATH="$SN89_REPO"
export SN89_DATA_ROOT
export SN89_STATE_DIR="${SN89_STATE_DIR:-$HOME/.sn89}"
exec "$SN89_PY" scripts/hf_auto_loop.py \
  --wallet.name "${SN89_WALLET_NAME:-rabi}" \
  --wallet.hotkey "${SN89_WALLET_HOTKEY:-rabi-h1}" \
  --pick-rank "${SN89_HF_PICK_RANK:-0}" \
  "$@"
