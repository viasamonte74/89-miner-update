#!/bin/bash
# PM2 entrypoint: long-running LF auto-submit (3/day, 1h gap).
# miner.env first, then re-apply launcher overrides.
set -euo pipefail
SN89_REPO="${SN89_REPO:-/workspace/InfiniteQuant-Subnet}"
SN89_DATA_ROOT="${SN89_DATA_ROOT:-/workspace/sn89_data}"
SN89_PY="${SN89_PY:-/venv/sn89/bin/python}"
cd "$SN89_REPO"

_OV_WALLET_NAME="${SN89_WALLET_NAME:-}"
_OV_WALLET_HOTKEY="${SN89_WALLET_HOTKEY:-}"
_OV_PICK="${SN89_AUTO_PICK_RANK:-}"
_OV_STATE="${SN89_AUTO_STATE:-}"
_OV_LOG="${SN89_AUTO_LOG:-}"
_OV_HF_LOG="${SN89_HF_AUTO_LOG:-}"
_OV_STOP="${SN89_AUTO_STOP:-}"
_OV_SIB="${SN89_AUTO_SIBLING_LOGS:-}"
_OV_CLAIM="${SN89_AUTO_CLAIM_DIR:-}"
_OV_HF_CLAIM="${SN89_HF_CLAIM_DIR:-}"
_OV_STRICT="${SN89_STRICT_MODE:-}"
_OV_PREF="${SN89_STRICT_PREFER_SHORT:-}"
_OV_QF="${SN89_LF_QUALITY_FLOOR:-}"
_OV_WASH="${SN89_LF_WASH_AVOID:-}"
_OV_QONLY="${SN89_LF_QUALITY_ONLY:-}"
_OV_PRIORS="${SN89_FLEET_PRIORS_MODE:-}"
_OV_STATE_DIR="${SN89_STATE_DIR:-}"
_OV_FLEET="${SN89_FLEET_HOTKEYS:-}"

set -a
# shellcheck disable=SC1091
source "$SN89_DATA_ROOT/miner.env"
# shellcheck disable=SC1091
[[ -f "$SN89_DATA_ROOT/secrets.env" ]] && source "$SN89_DATA_ROOT/secrets.env"
# shellcheck disable=SC1091
[[ -f "$SN89_DATA_ROOT/fleet_hotkeys.env" ]] && source "$SN89_DATA_ROOT/fleet_hotkeys.env"
set +a

[[ -n "$_OV_WALLET_NAME" ]] && export SN89_WALLET_NAME="$_OV_WALLET_NAME"
[[ -n "$_OV_WALLET_HOTKEY" ]] && export SN89_WALLET_HOTKEY="$_OV_WALLET_HOTKEY"
[[ -n "$_OV_PICK" ]] && export SN89_AUTO_PICK_RANK="$_OV_PICK"
[[ -n "$_OV_STATE" ]] && export SN89_AUTO_STATE="$_OV_STATE"
[[ -n "$_OV_LOG" ]] && export SN89_AUTO_LOG="$_OV_LOG"
[[ -n "$_OV_HF_LOG" ]] && export SN89_HF_AUTO_LOG="$_OV_HF_LOG"
[[ -n "$_OV_STOP" ]] && export SN89_AUTO_STOP="$_OV_STOP"
[[ -n "$_OV_SIB" ]] && export SN89_AUTO_SIBLING_LOGS="$_OV_SIB"
[[ -n "$_OV_CLAIM" ]] && export SN89_AUTO_CLAIM_DIR="$_OV_CLAIM"
[[ -n "$_OV_HF_CLAIM" ]] && export SN89_HF_CLAIM_DIR="$_OV_HF_CLAIM"
[[ -n "$_OV_STRICT" ]] && export SN89_STRICT_MODE="$_OV_STRICT"
[[ -n "$_OV_PREF" ]] && export SN89_STRICT_PREFER_SHORT="$_OV_PREF"
[[ -n "$_OV_QF" ]] && export SN89_LF_QUALITY_FLOOR="$_OV_QF"
[[ -n "$_OV_WASH" ]] && export SN89_LF_WASH_AVOID="$_OV_WASH"
[[ -n "$_OV_QONLY" ]] && export SN89_LF_QUALITY_ONLY="$_OV_QONLY"
[[ -n "$_OV_PRIORS" ]] && export SN89_FLEET_PRIORS_MODE="$_OV_PRIORS"
[[ -n "$_OV_STATE_DIR" ]] && export SN89_STATE_DIR="$_OV_STATE_DIR"
[[ -n "$_OV_FLEET" ]] && export SN89_FLEET_HOTKEYS="$_OV_FLEET"

export PYTHONPATH="$SN89_REPO"
export SN89_DATA_ROOT
export SN89_STATE_DIR="${SN89_STATE_DIR:-$HOME/.sn89}"
exec "$SN89_PY" scripts/lf_auto_loop.py \
  --wallet.name "${SN89_WALLET_NAME:-rabi}" \
  --wallet.hotkey "${SN89_WALLET_HOTKEY:-rabi-h1}" \
  --direction "${SN89_AUTO_DIRECTION:-AUTO}" \
  --pick-rank "${SN89_AUTO_PICK_RANK:-0}" \
  "$@"
