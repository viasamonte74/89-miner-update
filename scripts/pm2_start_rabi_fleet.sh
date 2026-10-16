#!/bin/bash
# Start/restart rabi-h1/h2/h3(/h4) LF + HF concurrently.
# LF: fleet claims + network live anti-copy
# HF: diversity guard + skips this hotkey's LF pairs (cross-mech 24h lock)
#
# Does NOT source miner.env here (avoids writing SN89_FEED_TOKEN into
# ~/.pm2/dump.pm2 on `pm2 save`). Wrappers pm2_lf_auto.sh / pm2_hf_auto.sh
# load miner.env inside each process.
set -euo pipefail

SN89_REPO="${SN89_REPO:-/workspace/InfiniteQuant-Subnet}"
SN89_DATA_ROOT="${SN89_DATA_ROOT:-/workspace/sn89_data}"
SN89_STATE_DIR="${SN89_STATE_DIR:-$HOME/.sn89}"
SN89_LOG_DIR="${SN89_LOG_DIR:-/workspace/logs}"
SN89_PY="${SN89_PY:-/venv/sn89/bin/python}"
# Optional nvm (only if present — not required for fleet loops)
if [[ -f /opt/nvm/nvm.sh ]]; then
  # shellcheck disable=SC1091
  . /opt/nvm/nvm.sh
fi

cd "$SN89_REPO"
mkdir -p "$SN89_DATA_ROOT/fleet_claims" "$SN89_DATA_ROOT/hf_fleet_claims" \
  "$SN89_LOG_DIR" "$SN89_STATE_DIR"

LOG_H1="$SN89_LOG_DIR/lf_auto_submit_h1.jsonl"
LOG_H2="$SN89_LOG_DIR/lf_auto_submit_h2.jsonl"
LOG_H3="$SN89_LOG_DIR/lf_auto_submit_h3.jsonl"
LOG_H4="$SN89_LOG_DIR/lf_auto_submit_h4.jsonl"
HF_LOG_H1="$SN89_LOG_DIR/hf_auto_submit_h1.jsonl"
HF_LOG_H2="$SN89_LOG_DIR/hf_auto_submit_h2.jsonl"
HF_LOG_H3="$SN89_LOG_DIR/hf_auto_submit_h3.jsonl"
HF_LOG_H4="$SN89_LOG_DIR/hf_auto_submit_h4.jsonl"
if [[ ! -f "$LOG_H1" && -f "$SN89_LOG_DIR/lf_auto_submit.jsonl" ]]; then
  cp -a "$SN89_LOG_DIR/lf_auto_submit.jsonl" "$LOG_H1"
fi

# Include h4 in fleet only once it is registered on netuid 89
INCLUDE_H4=0
if "$SN89_PY" - <<'PY'
import bittensor as bt
w = bt.Wallet(name="rabi", hotkey="rabi-h4")
meta = bt.Subtensor(network="finney").metagraph(netuid=89)
raise SystemExit(0 if w.hotkey.ss58_address in meta.hotkeys else 1)
PY
then
  INCLUDE_H4=1
  echo "h4 registered — including in fleet"
else
  echo "h4 not registered — skip (register via wallet tools; see README)"
fi

export PYTHONPATH="$SN89_REPO"
export SN89_DATA_ROOT SN89_STATE_DIR SN89_REPO SN89_PY

# Seed claims from recent sibling submits (no secrets)
"$SN89_PY" - <<PY
import json, time
from pathlib import Path
import bittensor as bt

def ss58(name):
    try:
        return bt.Wallet(name="rabi", hotkey=name).hotkey.ss58_address
    except Exception:
        return ""

hk_map = {k: ss58(k) for k in ("rabi-h1", "rabi-h2", "rabi-h3", "rabi-h4")}
log_hk = {
    Path("$LOG_H1"): hk_map["rabi-h1"],
    Path("$LOG_H2"): hk_map["rabi-h2"],
    Path("$LOG_H3"): hk_map["rabi-h3"],
    Path("$LOG_H4"): hk_map["rabi-h4"],
    Path("$HF_LOG_H1"): hk_map["rabi-h1"],
    Path("$HF_LOG_H2"): hk_map["rabi-h2"],
    Path("$HF_LOG_H3"): hk_map["rabi-h3"],
    Path("$HF_LOG_H4"): hk_map["rabi-h4"],
}
claim_dir = Path("$SN89_DATA_ROOT/fleet_claims")
claim_dir.mkdir(parents=True, exist_ok=True)
now = time.time()
for path, hotkey in list(log_hk.items())[:4]:
    if not path.exists() or not hotkey:
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        if now - ts > 8 * 3600:
            continue
        pair = (o.get("pair") or "").upper()
        direction = (o.get("direction") or "").upper()
        if not pair or not direction:
            continue
        key = f"{pair}_{direction}"
        payload = {
            "pair": pair,
            "direction": direction,
            "hotkey": hotkey,
            "ts": ts,
            "until": ts + 8 * 3600,
            "log": str(path),
        }
        (claim_dir / f"{key}.json").write_text(json.dumps(payload, indent=2))
print("seeded LF claims:", sorted(p.name for p in claim_dir.glob("*.json")))
hf_claim = Path("$SN89_DATA_ROOT/hf_fleet_claims")
hf_claim.mkdir(parents=True, exist_ok=True)
for path, hotkey in list(log_hk.items())[4:]:
    if not path.exists() or not hotkey:
        continue
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        hz = float(o.get("horizon_s") or 1800)
        if now - ts > hz:
            continue
        pair = (o.get("pair") or "").upper()
        direction = (o.get("direction") or "").upper()
        if not pair or not direction:
            continue
        key = f"{pair}_{direction}"
        payload = {
            "pair": pair,
            "direction": direction,
            "hotkey": hotkey,
            "ts": ts,
            "until": ts + hz,
            "log": str(path),
        }
        (hf_claim / f"{key}.json").write_text(json.dumps(payload, indent=2))
print("seeded HF claims:", sorted(p.name for p in hf_claim.glob("*.json")))
PY

start_lf() {
  local name="$1" hotkey="$2" rank="$3" state="$4" log="$5" stop="$6" siblings="$7" hf_log="$8"
  local quality_floor="${9:-0}"
  local wash_avoid="${10:-0}"
  pm2 delete "$name" >/dev/null 2>&1 || true
  SN89_REPO="$SN89_REPO" \
  SN89_DATA_ROOT="$SN89_DATA_ROOT" \
  SN89_STATE_DIR="$SN89_STATE_DIR" \
  SN89_PY="$SN89_PY" \
  SN89_WALLET_NAME=rabi \
  SN89_WALLET_HOTKEY="$hotkey" \
  SN89_AUTO_PICK_RANK="$rank" \
  SN89_AUTO_STATE="$state" \
  SN89_AUTO_LOG="$log" \
  SN89_HF_AUTO_LOG="$hf_log" \
  SN89_HF_CLAIM_DIR="$SN89_DATA_ROOT/hf_fleet_claims" \
  SN89_AUTO_STOP="$stop" \
  SN89_AUTO_SIBLING_LOGS="$siblings" \
  SN89_AUTO_CLAIM_DIR="$SN89_DATA_ROOT/fleet_claims" \
  SN89_STRICT_MODE=0 \
  SN89_STRICT_PREFER_SHORT=0 \
  SN89_LF_QUALITY_FLOOR="$quality_floor" \
  SN89_LF_WASH_AVOID="$wash_avoid" \
  SN89_LF_WASH_AVOID_PAIRS=BTCUSD \
  SN89_LF_BLOCK_SIBLING_OPPOSITE=1 \
  SN89_FLEET_HOTKEYS="${SN89_FLEET_HOTKEYS:-}" \
  SN89_NETWORK_LIVE_BLOCK=1 \
  pm2 start "$SN89_REPO/scripts/pm2_lf_auto.sh" \
    --name "$name" \
    --interpreter bash
}

start_hf() {
  local name="$1" hotkey="$2" rank="$3" state="$4" log="$5" stop="$6" siblings="$7" lf_log="$8"
  local strict="${9:-0}"
  pm2 delete "$name" >/dev/null 2>&1 || true
  SN89_REPO="$SN89_REPO" \
  SN89_DATA_ROOT="$SN89_DATA_ROOT" \
  SN89_STATE_DIR="$SN89_STATE_DIR" \
  SN89_PY="$SN89_PY" \
  SN89_WALLET_NAME=rabi \
  SN89_WALLET_HOTKEY="$hotkey" \
  SN89_HF_PICK_RANK="$rank" \
  SN89_HF_AUTO_STATE="$state" \
  SN89_HF_AUTO_LOG="$log" \
  SN89_HF_AUTO_STOP="$stop" \
  SN89_HF_SIBLING_LOGS="$siblings" \
  SN89_HF_CLAIM_DIR="$SN89_DATA_ROOT/hf_fleet_claims" \
  SN89_AUTO_LOG="$lf_log" \
  SN89_AUTO_CLAIM_DIR="$SN89_DATA_ROOT/fleet_claims" \
  SN89_HF_FREE_BEST=1 \
  SN89_STRICT_MODE="$strict" \
  SN89_STRICT_BOTH_DIRS=1 \
  SN89_HF_DIVERSITY_GUARD=1 \
  SN89_HF_DIVERSITY_GUARD_MODE=improve \
  SN89_HF_DIVERSITY_MARGIN=0.05 \
  SN89_HF_ALLOW_MINORITY_EDGE=0 \
  SN89_HF_DIR_MODE=fade_spike \
  SN89_HF_FADE_SPIKE_MIN=5 \
  SN89_HF_FADE_SPIKE_Z=2.0 \
  SN89_HF_BLOCK_SIBLING_OPPOSITE=0 \
  SN89_HF_REJECT_COOLDOWN_S=900 \
  SN89_HF_LIVE_QUOTES=1 \
  SN89_HF_MAX_HORIZON_S=1800 \
  SN89_HF_BAR_REFRESH_S=60 \
  SN89_HF_BAR_MAX_AGE_MIN=12 \
  SN89_HF_BAR_HARD_MAX_AGE_MIN=45 \
  SN89_HF_AUTO_PAIRS=SUIUSD,ALGOUSD,ADAUSD,KPEPEUSD,NEARUSD \
  SN89_HF_MAX_PER_DAY=20 \
  SN89_HF_MAX_PER_PAIR_PER_DAY=6 \
  SN89_HF_MIN_GAP_S=120 \
  pm2 start "$SN89_REPO/scripts/pm2_hf_auto.sh" \
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

FLEET_HOTKEYS="$("$SN89_PY" - <<'PY'
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
  > "$SN89_DATA_ROOT/fleet_hotkeys.env"
echo "SN89_FLEET_HOTKEYS=$SN89_FLEET_HOTKEYS"

# h1: wash-avoid BTC (no extra volume). h2/h3: quality floor, both dirs (no SHORT-only).
start_lf sn89-rabi-h1 rabi-h1 0 \
  "$SN89_STATE_DIR/lf_auto_state_h1.json" "$LOG_H1" \
  "$SN89_DATA_ROOT/STOP_AUTO_SUBMIT_H1" \
  "$SIB_LF_1" \
  "$HF_LOG_H1" 0 1

start_lf sn89-rabi-h2 rabi-h2 0 \
  "$SN89_STATE_DIR/lf_auto_state_h2.json" "$LOG_H2" \
  "$SN89_DATA_ROOT/STOP_AUTO_SUBMIT_H2" \
  "$SIB_LF_2" \
  "$HF_LOG_H2" 0.85 0

start_lf sn89-rabi-h3 rabi-h3 0 \
  "$SN89_STATE_DIR/lf_auto_state_h3.json" "$LOG_H3" \
  "$SN89_DATA_ROOT/STOP_AUTO_SUBMIT_H3" \
  "$SIB_LF_3" \
  "$HF_LOG_H3" 0.85 0

if [[ "$INCLUDE_H4" == "1" ]]; then
  start_lf sn89-rabi-h4 rabi-h4 0 \
    "$SN89_STATE_DIR/lf_auto_state_h4.json" "$LOG_H4" \
    "$SN89_DATA_ROOT/STOP_AUTO_SUBMIT_H4" \
    "${LOG_H1}:${LOG_H2}:${LOG_H3}" \
    "$HF_LOG_H4" 0 0
fi

start_hf sn89-rabi-hf-h1 rabi-h1 0 \
  "$SN89_STATE_DIR/hf_auto_state_h1.json" "$HF_LOG_H1" \
  "$SN89_DATA_ROOT/STOP_HF_AUTO_H1" \
  "$SIB_HF_1" \
  "$LOG_H1" 0

start_hf sn89-rabi-hf-h2 rabi-h2 1 \
  "$SN89_STATE_DIR/hf_auto_state_h2.json" "$HF_LOG_H2" \
  "$SN89_DATA_ROOT/STOP_HF_AUTO_H2" \
  "$SIB_HF_2" \
  "$LOG_H2" 0

start_hf sn89-rabi-hf-h3 rabi-h3 2 \
  "$SN89_STATE_DIR/hf_auto_state_h3.json" "$HF_LOG_H3" \
  "$SN89_DATA_ROOT/STOP_HF_AUTO_H3" \
  "$SIB_HF_3" \
  "$LOG_H3" 0

if [[ "$INCLUDE_H4" == "1" ]]; then
  start_hf sn89-rabi-hf-h4 rabi-h4 3 \
    "$SN89_STATE_DIR/hf_auto_state_h4.json" "$HF_LOG_H4" \
    "$SN89_DATA_ROOT/STOP_HF_AUTO_H4" \
    "${HF_LOG_H1}:${HF_LOG_H2}:${HF_LOG_H3}" \
    "$LOG_H4" 1
fi

# Save process list only — wrappers load miner.env themselves, so dump.pm2 should
# not contain SN89_FEED_TOKEN from this launcher. Operator scrub if an old dump
# still has it:
#   grep -n FEED_TOKEN ~/.pm2/dump.pm2 && echo "edit/remove those env entries, then pm2 resurrect"
pm2 save
pm2 list
echo "Fleet OK: LF+HF (h1/h2/h3$([ "$INCLUDE_H4" = 1 ] && echo '/h4')). Tokens load only inside pm2_*_auto.sh."
