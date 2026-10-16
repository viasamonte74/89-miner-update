#!/bin/bash
# PM2 entrypoint: long-running HF auto-submit (concurrent with LF on same hotkey).
# Load miner.env first, then re-apply per-process overrides so miner.env cannot
# clobber SN89_STRICT_* / state paths set by the fleet launcher.
set -euo pipefail
SN89_REPO="${SN89_REPO:-/workspace/InfiniteQuant-Subnet}"
SN89_DATA_ROOT="${SN89_DATA_ROOT:-/workspace/sn89_data}"
SN89_PY="${SN89_PY:-/venv/sn89/bin/python}"
cd "$SN89_REPO"

# Snapshot launcher-provided overrides before sourcing miner.env
_OV_WALLET_NAME="${SN89_WALLET_NAME:-}"
_OV_WALLET_HOTKEY="${SN89_WALLET_HOTKEY:-}"
_OV_PICK="${SN89_HF_PICK_RANK:-}"
_OV_STATE="${SN89_HF_AUTO_STATE:-}"
_OV_LOG="${SN89_HF_AUTO_LOG:-}"
_OV_STOP="${SN89_HF_AUTO_STOP:-}"
_OV_SIB="${SN89_HF_SIBLING_LOGS:-}"
_OV_CLAIM="${SN89_HF_CLAIM_DIR:-}"
_OV_LF_LOG="${SN89_AUTO_LOG:-}"
_OV_LF_CLAIM="${SN89_AUTO_CLAIM_DIR:-}"
_OV_STRICT="${SN89_STRICT_MODE:-}"
_OV_BOTH="${SN89_STRICT_BOTH_DIRS:-}"
_OV_STATE_DIR="${SN89_STATE_DIR:-}"
_OV_PAIRS="${SN89_HF_AUTO_PAIRS:-}"
_OV_GUARD="${SN89_HF_DIVERSITY_GUARD:-}"
_OV_MODE="${SN89_HF_DIVERSITY_GUARD_MODE:-}"
_OV_LIVE="${SN89_HF_LIVE_QUOTES:-}"
_OV_OPP="${SN89_HF_BLOCK_SIBLING_OPPOSITE:-}"
_OV_MAXH="${SN89_HF_MAX_HORIZON_S:-}"

set -a
# shellcheck disable=SC1091
source "$SN89_DATA_ROOT/miner.env"
set +a

# Re-apply overrides (non-empty launcher values win)
[[ -n "$_OV_WALLET_NAME" ]] && export SN89_WALLET_NAME="$_OV_WALLET_NAME"
[[ -n "$_OV_WALLET_HOTKEY" ]] && export SN89_WALLET_HOTKEY="$_OV_WALLET_HOTKEY"
[[ -n "$_OV_PICK" ]] && export SN89_HF_PICK_RANK="$_OV_PICK"
[[ -n "$_OV_STATE" ]] && export SN89_HF_AUTO_STATE="$_OV_STATE"
[[ -n "$_OV_LOG" ]] && export SN89_HF_AUTO_LOG="$_OV_LOG"
[[ -n "$_OV_STOP" ]] && export SN89_HF_AUTO_STOP="$_OV_STOP"
[[ -n "$_OV_SIB" ]] && export SN89_HF_SIBLING_LOGS="$_OV_SIB"
[[ -n "$_OV_CLAIM" ]] && export SN89_HF_CLAIM_DIR="$_OV_CLAIM"
[[ -n "$_OV_LF_LOG" ]] && export SN89_AUTO_LOG="$_OV_LF_LOG"
[[ -n "$_OV_LF_CLAIM" ]] && export SN89_AUTO_CLAIM_DIR="$_OV_LF_CLAIM"
[[ -n "$_OV_STRICT" ]] && export SN89_STRICT_MODE="$_OV_STRICT"
[[ -n "$_OV_BOTH" ]] && export SN89_STRICT_BOTH_DIRS="$_OV_BOTH"
[[ -n "$_OV_STATE_DIR" ]] && export SN89_STATE_DIR="$_OV_STATE_DIR"
[[ -n "$_OV_PAIRS" ]] && export SN89_HF_AUTO_PAIRS="$_OV_PAIRS"
[[ -n "$_OV_GUARD" ]] && export SN89_HF_DIVERSITY_GUARD="$_OV_GUARD"
[[ -n "$_OV_MODE" ]] && export SN89_HF_DIVERSITY_GUARD_MODE="$_OV_MODE"
[[ -n "$_OV_LIVE" ]] && export SN89_HF_LIVE_QUOTES="$_OV_LIVE"
[[ -n "$_OV_OPP" ]] && export SN89_HF_BLOCK_SIBLING_OPPOSITE="$_OV_OPP"
[[ -n "$_OV_MAXH" ]] && export SN89_HF_MAX_HORIZON_S="$_OV_MAXH"

export PYTHONPATH="$SN89_REPO"
export SN89_DATA_ROOT
export SN89_STATE_DIR="${SN89_STATE_DIR:-$HOME/.sn89}"
exec "$SN89_PY" scripts/hf_auto_loop.py \
  --wallet.name "${SN89_WALLET_NAME:-rabi}" \
  --wallet.hotkey "${SN89_WALLET_HOTKEY:-rabi-h1}" \
  --pick-rank "${SN89_HF_PICK_RANK:-0}" \
  "$@"
