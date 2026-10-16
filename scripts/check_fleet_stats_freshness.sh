#!/bin/bash
# Exit 1 if fleet_pair_stats.json is missing/stale; print age. Schedule via cron:
#   0 */6 * * * /workspace/InfiniteQuant-Subnet/scripts/check_fleet_stats_freshness.sh \
#     || /workspace/InfiniteQuant-Subnet/scripts/refresh_fleet_pair_stats.sh
set -euo pipefail
SN89_REPO="${SN89_REPO:-/workspace/InfiniteQuant-Subnet}"
SN89_PY="${SN89_PY:-/venv/sn89/bin/python}"
export SN89_FLEET_STATS="${SN89_FLEET_STATS:-/workspace/sn89_data/fleet_pair_stats.json}"
export SN89_FLEET_STATS_MAX_AGE_H="${SN89_FLEET_STATS_MAX_AGE_H:-48}"
cd "$SN89_REPO"
exec "$SN89_PY" - <<'PY'
import os, sys
sys.path.insert(0, "scripts")
import fleet_priors as fp
age = fp.stats_age_hours()
max_h = float(os.environ.get("SN89_FLEET_STATS_MAX_AGE_H", "48"))
path = fp.STATS_PATH
if age is None:
    print(f"STALE missing_or_unreadable path={path}")
    sys.exit(1)
print(f"stats_age_h={age:.2f} max_h={max_h} path={path}")
sys.exit(1 if age > max_h else 0)
PY
