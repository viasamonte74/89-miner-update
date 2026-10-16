#!/usr/bin/env bash
# Bootstrap research env + data dirs on this Vast box.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export WORKSPACE="${WORKSPACE:-/workspace}"
export SN89_DATA_ROOT="${SN89_DATA_ROOT:-$WORKSPACE/sn89_data}"

mkdir -p "$SN89_DATA_ROOT"/{raw_windows,parquet} \
  "$ROOT/miner_ext"/{models,results} \
  "$WORKSPACE/logs"

source /venv/main/bin/activate
cd "$ROOT"
uv pip install pyarrow pandas requests pyyaml tqdm pytest -q

echo "bootstrap ok"
echo "  repo=$ROOT"
echo "  data=$SN89_DATA_ROOT"
python -m miner_ext.scripts.wilson_table | head -20
