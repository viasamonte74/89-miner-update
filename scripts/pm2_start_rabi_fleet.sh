#!/bin/bash
# Start/restart rabi-h1/h2/h3 LF + HF concurrently.
# LF: pick_rank 0/1/2 + sibling logs + fleet_claims anti-copy
# HF: same diversification; skips this hotkey's live LF pairs (cross-mech lock)
set -euo pipefail
. /opt/nvm/nvm.sh
cd /workspace/InfiniteQuant-Subnet
set -a
# shellcheck disable=SC1091
source /workspace/sn89_data/miner.env
set +a

LOG_H1=/workspace/logs/lf_auto_submit_h1.jsonl
LOG_H2=/workspace/logs/lf_auto_submit_h2.jsonl
LOG_H3=/workspace/logs/lf_auto_submit_h3.jsonl
LOG_H4=/workspace/logs/lf_auto_submit_h4.jsonl
HF_LOG_H1=/workspace/logs/hf_auto_submit_h1.jsonl
HF_LOG_H2=/workspace/logs/hf_auto_submit_h2.jsonl
HF_LOG_H3=/workspace/logs/hf_auto_submit_h3.jsonl
HF_LOG_H4=/workspace/logs/hf_auto_submit_h4.jsonl
# migrate legacy h1 log once
if [[ ! -f "$LOG_H1" && -f /workspace/logs/lf_auto_submit.jsonl ]]; then
  cp -a /workspace/logs/lf_auto_submit.jsonl "$LOG_H1"
fi
mkdir -p /workspace/sn89_data/fleet_claims /workspace/sn89_data/hf_fleet_claims \
  /workspace/logs /root/.sn89

# Include h4 in fleet only once it is registered on netuid 89
INCLUDE_H4=0
if source /venv/sn89/bin/activate && python - <<'PY'
import bittensor as bt
w = bt.Wallet(name="rabi", hotkey="rabi-h4")
meta = bt.Subtensor(network="finney").metagraph(netuid=89)
raise SystemExit(0 if w.hotkey.ss58_address in meta.hotkeys else 1)
PY
then
  INCLUDE_H4=1
  echo "h4 registered — including in fleet"
else
  echo "h4 not registered — skip (claim first via scripts/referral_onboard.py)"
fi

# Seed LF claims from recent sibling submits so mid-day restarts stay diversified
source /venv/sn89/bin/activate
export PYTHONPATH=/workspace/InfiniteQuant-Subnet
python - <<'PY'
import json, time
from pathlib import Path
claim_dir = Path("/workspace/sn89_data/fleet_claims")
claim_dir.mkdir(parents=True, exist_ok=True)
now = time.time()
live_s = float(__import__("os").environ.get("SN89_AUTO_SIBLING_LIVE_S", "28800"))
logs = {
    "h1": Path("/workspace/logs/lf_auto_submit_h1.jsonl"),
    "h2": Path("/workspace/logs/lf_auto_submit_h2.jsonl"),
    "h3": Path("/workspace/logs/lf_auto_submit_h3.jsonl"),
}
if not logs["h1"].exists():
    logs["h1"] = Path("/workspace/logs/lf_auto_submit.jsonl")
for label, path in logs.items():
    if not path.exists():
        continue
    for line in path.read_text().splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        if now - ts > live_s:
            continue
        pair, direction = o.get("pair"), o.get("direction")
        if not pair or not direction:
            continue
        key = f"{pair.upper()}_{direction.upper()}"
        payload = {
            "pair": pair.upper(),
            "direction": direction.upper(),
            "hotkey": f"seed-{label}",
            "ts": ts,
            "until": ts + live_s,
            "log": str(path),
        }
        (claim_dir / f"{key}.json").write_text(json.dumps(payload, indent=2))
print("seeded LF claims:", sorted(p.name for p in claim_dir.glob("*.json")))

# Seed HF claims (use each receipt's horizon_s when present)
hf_claim = Path("/workspace/sn89_data/hf_fleet_claims")
hf_claim.mkdir(parents=True, exist_ok=True)
hf_logs = {
    "h1": Path("/workspace/logs/hf_auto_submit_h1.jsonl"),
    "h2": Path("/workspace/logs/hf_auto_submit_h2.jsonl"),
    "h3": Path("/workspace/logs/hf_auto_submit_h3.jsonl"),
}
for label, path in hf_logs.items():
    if not path.exists():
        continue
    for line in path.read_text().splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        hz = float(o.get("horizon_s") or 7200)
        if now - ts > hz:
            continue
        pair, direction = o.get("pair"), o.get("direction")
        if not pair or not direction:
            continue
        key = f"{pair.upper()}_{direction.upper()}"
        payload = {
            "pair": pair.upper(),
            "direction": direction.upper(),
            "hotkey": f"seed-hf-{label}",
            "ts": ts,
            "until": ts + hz,
            "log": str(path),
        }
        (hf_claim / f"{key}.json").write_text(json.dumps(payload, indent=2))
print("seeded HF claims:", sorted(p.name for p in hf_claim.glob("*.json")))
PY

start_lf() {
  local name="$1" hotkey="$2" rank="$3" state="$4" log="$5" stop="$6" siblings="$7" hf_log="$8"
  local strict="${9:-0}"
  pm2 delete "$name" >/dev/null 2>&1 || true
  SN89_WALLET_NAME=rabi \
  SN89_WALLET_HOTKEY="$hotkey" \
  SN89_AUTO_PICK_RANK="$rank" \
  SN89_AUTO_STATE="$state" \
  SN89_AUTO_LOG="$log" \
  SN89_HF_AUTO_LOG="$hf_log" \
  SN89_HF_CLAIM_DIR=/workspace/sn89_data/hf_fleet_claims \
  SN89_AUTO_STOP="$stop" \
  SN89_AUTO_SIBLING_LOGS="$siblings" \
  SN89_AUTO_CLAIM_DIR=/workspace/sn89_data/fleet_claims \
  SN89_STRICT_MODE="$strict" \
  SN89_FLEET_HOTKEYS="${SN89_FLEET_HOTKEYS:-}" \
  SN89_NETWORK_LIVE_BLOCK=1 \
  pm2 start /workspace/InfiniteQuant-Subnet/scripts/pm2_lf_auto.sh \
    --name "$name" \
    --interpreter bash
}

start_hf() {
  local name="$1" hotkey="$2" rank="$3" state="$4" log="$5" stop="$6" siblings="$7" lf_log="$8"
  local strict="${9:-0}"
  pm2 delete "$name" >/dev/null 2>&1 || true
  SN89_WALLET_NAME=rabi \
  SN89_WALLET_HOTKEY="$hotkey" \
  SN89_HF_PICK_RANK="$rank" \
  SN89_HF_AUTO_STATE="$state" \
  SN89_HF_AUTO_LOG="$log" \
  SN89_HF_AUTO_STOP="$stop" \
  SN89_HF_SIBLING_LOGS="$siblings" \
  SN89_HF_CLAIM_DIR=/workspace/sn89_data/hf_fleet_claims \
  SN89_AUTO_LOG="$lf_log" \
  SN89_AUTO_CLAIM_DIR=/workspace/sn89_data/fleet_claims \
  SN89_HF_FREE_BEST=1 \
  SN89_STRICT_MODE="$strict" \
  SN89_STRICT_BOTH_DIRS=1 \
  pm2 start /workspace/InfiniteQuant-Subnet/scripts/pm2_hf_auto.sh \
    --name "$name" \
    --interpreter bash
}

SIB_LF_1="${LOG_H2}:${LOG_H3}"
SIB_LF_2="${LOG_H1}:${LOG_H3}"
SIB_LF_3="${LOG_H1}:${LOG_H2}"
SIB_HF_1="${HF_LOG_H2}:${HF_LOG_H3}"
SIB_HF_2="${HF_LOG_H1}:${HF_LOG_H3}"
SIB_HF_3="${HF_LOG_H1}:${HF_LOG_H2}"
if [[ "$INCLUDE_H4" == "1" ]]; then
  SIB_LF_1="${SIB_LF_1}:${LOG_H4}"
  SIB_LF_2="${SIB_LF_2}:${LOG_H4}"
  SIB_LF_3="${SIB_LF_3}:${LOG_H4}"
  SIB_HF_1="${SIB_HF_1}:${HF_LOG_H4}"
  SIB_HF_2="${SIB_HF_2}:${HF_LOG_H4}"
  SIB_HF_3="${SIB_HF_3}:${HF_LOG_H4}"
fi

# Resolve fleet ss58 once — LF network anti-copy excludes these from "others".
FLEET_HOTKEYS="$(
  source /venv/sn89/bin/activate
  python - <<'PY'
import bittensor as bt
addrs=[]
for hk in ["rabi-h1","rabi-h2","rabi-h3","rabi-h4"]:
    try:
        addrs.append(bt.Wallet(name="rabi", hotkey=hk).hotkey.ss58_address)
    except Exception:
        pass
print(",".join(addrs))
PY
)"
export SN89_FLEET_HOTKEYS="$FLEET_HOTKEYS"
printf 'export SN89_FLEET_HOTKEYS="%s"\n' "$FLEET_HOTKEYS" \
  > /workspace/sn89_data/fleet_hotkeys.env
echo "SN89_FLEET_HOTKEYS=$SN89_FLEET_HOTKEYS"

# pick_rank=0 for all: compete for best free setups; fleet claims + network
# live block prevent copy. strict=1 on h2/h3 = quality-or-skip (no loser scraps).
start_lf sn89-rabi-h1 rabi-h1 0 \
  /root/.sn89/lf_auto_state_h1.json "$LOG_H1" \
  /workspace/sn89_data/STOP_AUTO_SUBMIT_H1 \
  "$SIB_LF_1" \
  "$HF_LOG_H1" 0

start_lf sn89-rabi-h2 rabi-h2 0 \
  /root/.sn89/lf_auto_state_h2.json "$LOG_H2" \
  /workspace/sn89_data/STOP_AUTO_SUBMIT_H2 \
  "$SIB_LF_2" \
  "$HF_LOG_H2" 1

start_lf sn89-rabi-h3 rabi-h3 0 \
  /root/.sn89/lf_auto_state_h3.json "$LOG_H3" \
  /workspace/sn89_data/STOP_AUTO_SUBMIT_H3 \
  "$SIB_LF_3" \
  "$HF_LOG_H3" 1

if [[ "$INCLUDE_H4" == "1" ]]; then
  start_lf sn89-rabi-h4 rabi-h4 0 \
    /root/.sn89/lf_auto_state_h4.json "$LOG_H4" \
    /workspace/sn89_data/STOP_AUTO_SUBMIT_H4 \
    "${LOG_H1}:${LOG_H2}:${LOG_H3}" \
    "$HF_LOG_H4" 0
fi

start_hf sn89-rabi-hf-h1 rabi-h1 0 \
  /root/.sn89/hf_auto_state_h1.json "$HF_LOG_H1" \
  /workspace/sn89_data/STOP_HF_AUTO_H1 \
  "$SIB_HF_1" \
  "$LOG_H1" 0

start_hf sn89-rabi-hf-h2 rabi-h2 1 \
  /root/.sn89/hf_auto_state_h2.json "$HF_LOG_H2" \
  /workspace/sn89_data/STOP_HF_AUTO_H2 \
  "$SIB_HF_2" \
  "$LOG_H2" 0

start_hf sn89-rabi-hf-h3 rabi-h3 2 \
  /root/.sn89/hf_auto_state_h3.json "$HF_LOG_H3" \
  /workspace/sn89_data/STOP_HF_AUTO_H3 \
  "$SIB_HF_3" \
  "$LOG_H3" 0

if [[ "$INCLUDE_H4" == "1" ]]; then
  start_hf sn89-rabi-hf-h4 rabi-h4 3 \
    /root/.sn89/hf_auto_state_h4.json "$HF_LOG_H4" \
    /workspace/sn89_data/STOP_HF_AUTO_H4 \
    "${HF_LOG_H1}:${HF_LOG_H2}:${HF_LOG_H3}" \
    "$LOG_H4" 1
fi

# pm2 save dumps process env into ~/.pm2/dump.pm2 — keep tokens out of miner.env
# that is sourced into these processes, or scrub the dump after save.
pm2 save
pm2 list
echo "Fleet OK: LF+HF concurrent (h1/h2/h3$([ "$INCLUDE_H4" = 1 ] && echo '/h4')), referrer=h1 for new keys via referral_onboard.py"
