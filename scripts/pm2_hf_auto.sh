#!/bin/bash
# PM2 entrypoint: long-running HF auto-submit (concurrent with LF on same hotkey).
set -euo pipefail
cd /workspace/InfiniteQuant-Subnet
set -a
# shellcheck disable=SC1091
source /workspace/sn89_data/miner.env
set +a
export PYTHONPATH=/workspace/InfiniteQuant-Subnet
export SN89_DATA_ROOT=/workspace/sn89_data
exec /venv/sn89/bin/python scripts/hf_auto_loop.py \
  --wallet.name "${SN89_WALLET_NAME:-rabi}" \
  --wallet.hotkey "${SN89_WALLET_HOTKEY:-rabi-h1}" \
  --pick-rank "${SN89_HF_PICK_RANK:-0}" \
  "$@"
