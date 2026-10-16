#!/bin/bash
set -euo pipefail
cd /workspace/InfiniteQuant-Subnet
set -a
# shellcheck disable=SC1091
source /workspace/sn89_data/miner.env
set +a
export PYTHONPATH=/workspace/InfiniteQuant-Subnet
export SN89_DATA_ROOT=/workspace/sn89_data
exec /venv/sn89/bin/python neurons/miner.py "$@"
