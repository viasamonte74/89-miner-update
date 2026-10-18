"""Fleet priors — gated so small-sample noise cannot ban/boost scores.

Modes (SN89_FLEET_PRIORS_MODE):
  off     — default. All mult=1.0, no hard bans/hour blocks from this module.
  legacy  — old hand-tuned tables (unsafe; n≈2–5 per cell).
  wilson  — only apply a prior when decisive n ≥ SN89_FLEET_PRIORS_MIN_N (30)
            and Wilson lower bound clears the configured floor. Static tables
            alone never qualify; needs fleet_pair_stats.json cells with counts.

Hand-tuned 0/2 and 0/3 bans remove ZRO/TAO/ARB etc.; with a true 50% coin those
patterns appear by chance across ~30 combos. Do not re-enable legacy without a
walk-forward report under miner_ext/results/.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

STATS_PATH = Path(
    os.environ.get("SN89_FLEET_STATS", "/workspace/sn89_data/fleet_pair_stats.json")
)
_RAW_PRIORS_MODE = os.environ.get("SN89_FLEET_PRIORS_MODE", "off").strip().lower()
# Unknown values (typos from miner.env) → off, never silent legacy.
if _RAW_PRIORS_MODE in ("off", "legacy", "wilson"):
    PRIORS_MODE = _RAW_PRIORS_MODE
else:
    PRIORS_MODE = "off"
PRIORS_MIN_N = int(os.environ.get("SN89_FLEET_PRIORS_MIN_N", "30"))
# Wilson LB must exceed this to boost (>0.5) or fall below to ban/penalize.
PRIORS_WILSON_Z = float(os.environ.get("SN89_FLEET_PRIORS_WILSON_Z", "1.96"))
PRIORS_BOOST_LB = float(os.environ.get("SN89_FLEET_PRIORS_BOOST_LB", "0.55"))
PRIORS_BAN_UB = float(os.environ.get("SN89_FLEET_PRIORS_BAN_UB", "0.45"))

# --- legacy tables (only used when PRIORS_MODE=legacy) ---
LF_PAIR_MULT = {
    "AVAXUSD": 0.35,
    "BTCUSD": 0.12,
    "ETHUSD": 0.25,
    "SOLUSD": 0.55,
    "KPEPEUSD": 0.70,
    "NEARUSD": 0.85,
    "DOTUSD": 0.90,
    "TAOUSD": 1.05,
    "ZROUSD": 1.10,
    "ALGOUSD": 0.95,
    "LTCUSD": 1.10,
    "SUIUSD": 1.35,
    "BCHUSD": 1.15,
}
LF_PAIR_DENY = frozenset(
    {"AVAXUSD", "BTCUSD", "ETHUSD", "NEARUSD", "DOTUSD"}
)
LF_HOUR_MULT = {
    1: 1.35,
    8: 1.15,
    9: 1.20,
    19: 1.30,
    20: 1.25,
    3: 0.65,
    7: 0.55,
    10: 0.65,
    11: 0.50,
    12: 0.60,
    16: 0.70,
    17: 0.50,
    18: 0.85,
}
LF_HARD_AVOID_HOURS = frozenset({7, 11})
LF_SOFT_AVOID_HOURS = frozenset({3, 10, 12, 16, 17})
LF_SOFT_AVOID_SCORE_MULT = 1.40

HF_PAIR_DIR_MULT = {
    ("ADAUSD", "SHORT"): 1.45,
    ("SUIUSD", "SHORT"): 1.40,
    ("ALGOUSD", "SHORT"): 1.35,
    ("WLDUSD", "LONG"): 1.30,
    ("XRPUSD", "SHORT"): 1.25,
    ("NEARUSD", "SHORT"): 1.20,
    ("BCHUSD", "LONG"): 1.20,
    ("KPEPEUSD", "LONG"): 1.20,
    ("LINKUSD", "LONG"): 1.20,
    ("ALGOUSD", "LONG"): 1.10,
    ("SUIUSD", "LONG"): 1.00,
    ("AVAXUSD", "SHORT"): 0.95,
    ("AVAXUSD", "LONG"): 0.95,
    ("CRVUSD", "LONG"): 0.70,
    ("CRVUSD", "SHORT"): 0.60,
    ("WLDUSD", "SHORT"): 0.55,
    ("UNIUSD", "LONG"): 0.55,
    ("UNIUSD", "SHORT"): 0.55,
    ("DOTUSD", "SHORT"): 0.55,
    ("BNBUSD", "LONG"): 0.90,
    ("LINKUSD", "SHORT"): 0.30,
    ("TAOUSD", "SHORT"): 0.35,
    ("TAOUSD", "LONG"): 0.45,
    ("ZROUSD", "LONG"): 0.40,
    ("ZROUSD", "SHORT"): 0.40,
    ("ARBUSD", "LONG"): 0.40,
    ("ARBUSD", "SHORT"): 0.40,
    ("BNBUSD", "SHORT"): 0.35,
}
HF_PAIR_DIR_DENY = frozenset(
    {
        ("LINKUSD", "SHORT"),
        ("TAOUSD", "SHORT"),
        ("TAOUSD", "LONG"),
        ("ZROUSD", "LONG"),
        ("ZROUSD", "SHORT"),
        ("ARBUSD", "LONG"),
        ("ARBUSD", "SHORT"),
        ("BNBUSD", "SHORT"),
    }
)
HF_AVOID_HOURS = {1, 6, 8, 11, 12, 13, 14}
HF_HOUR_SCORE_MULT = {
    5: 1.25,
    3: 1.20,
    9: 1.15,
    2: 1.12,
    0: 1.08,
    4: 1.05,
    7: 1.05,
    10: 0.80,
    1: 0.55,
    6: 0.50,
    8: 0.40,
    11: 0.55,
    12: 0.55,
    13: 0.35,
    14: 0.35,
}

_stats_cache: dict | None = None


def utc_hour(now: float) -> int:
    return int((float(now) % 86_400) // 3600)


def wilson_lb(wins: int, n: int, z: float = 1.96) -> float:
    """Lower bound of Wilson score interval for a binomial proportion."""
    if n <= 0:
        return 0.0
    phat = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2.0 * n)
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return max(0.0, (centre - margin) / denom)


def wilson_ub(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 1.0
    phat = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2.0 * n)
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return min(1.0, (centre + margin) / denom)


def _load_stats() -> dict:
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    try:
        _stats_cache = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        _stats_cache = {}
    return _stats_cache


def _cell_wl(kind: str, key: str) -> tuple[int, int] | None:
    """Return (wins, decisive_n) from fleet_pair_stats.json if present."""
    stats = _load_stats()
    rows = stats.get(kind) or []
    for row in rows:
        if str(row.get("k")) != key:
            continue
        w = int(row.get("w") or 0)
        l = int(row.get("l") or 0)
        return w, w + l
    return None


def _wilson_mult_from_cell(kind: str, key: str) -> float:
    cell = _cell_wl(kind, key)
    if cell is None:
        return 1.0
    w, n = cell
    if n < PRIORS_MIN_N:
        return 1.0
    lb = wilson_lb(w, n, PRIORS_WILSON_Z)
    ub = wilson_ub(w, n, PRIORS_WILSON_Z)
    if lb >= PRIORS_BOOST_LB:
        # Mild boost only — backtest must justify larger moves.
        return 1.15
    if ub <= PRIORS_BAN_UB:
        return 0.85
    return 1.0


def _wilson_blocked_from_cell(kind: str, key: str) -> bool:
    cell = _cell_wl(kind, key)
    if cell is None:
        return False
    w, n = cell
    if n < PRIORS_MIN_N:
        return False
    return wilson_ub(w, n, PRIORS_WILSON_Z) <= PRIORS_BAN_UB


def lf_pair_mult(pair: str) -> float:
    if PRIORS_MODE == "off":
        return 1.0
    if PRIORS_MODE == "wilson":
        return _wilson_mult_from_cell("lf_pair", str(pair).upper())
    return float(LF_PAIR_MULT.get(str(pair).upper(), 1.0))


def lf_pair_blocked(pair: str) -> bool:
    if PRIORS_MODE == "off":
        return False
    if PRIORS_MODE == "wilson":
        return _wilson_blocked_from_cell("lf_pair", str(pair).upper())
    return str(pair).upper() in LF_PAIR_DENY


def lf_hour_mult(now: float) -> float:
    if PRIORS_MODE == "off":
        return 1.0
    if PRIORS_MODE == "wilson":
        return _wilson_mult_from_cell("lf_hour", str(utc_hour(now)))
    return float(LF_HOUR_MULT.get(utc_hour(now), 1.0))


def lf_hour_blocked(now: float, *, score: float, score_floor: float) -> bool:
    if PRIORS_MODE == "off":
        return False
    if PRIORS_MODE == "wilson":
        return _wilson_blocked_from_cell("lf_hour", str(utc_hour(now)))
    h = utc_hour(now)
    if h in LF_HARD_AVOID_HOURS:
        return True
    if h in LF_SOFT_AVOID_HOURS:
        return float(score) < float(score_floor) * LF_SOFT_AVOID_SCORE_MULT
    return False


def hf_pair_dir_mult(pair: str, direction: str) -> float:
    if PRIORS_MODE == "off":
        return 1.0
    key = f"{str(pair).upper()}:{str(direction).upper()}"
    if PRIORS_MODE == "wilson":
        return _wilson_mult_from_cell("hf_pair_dir", key)
    return float(
        HF_PAIR_DIR_MULT.get((str(pair).upper(), str(direction).upper()), 1.0)
    )


def hf_pair_dir_blocked(pair: str, direction: str) -> bool:
    if PRIORS_MODE == "off":
        return False
    key = f"{str(pair).upper()}:{str(direction).upper()}"
    if PRIORS_MODE == "wilson":
        return _wilson_blocked_from_cell("hf_pair_dir", key)
    return (str(pair).upper(), str(direction).upper()) in HF_PAIR_DIR_DENY


def hf_hour_mult(now: float) -> float:
    if PRIORS_MODE == "off":
        return 1.0
    if PRIORS_MODE == "wilson":
        return _wilson_mult_from_cell("hf_hour", str(utc_hour(now)))
    return float(HF_HOUR_SCORE_MULT.get(utc_hour(now), 1.0))


def hf_hour_blocked(now: float, *, score: float, min_score: float) -> bool:
    if PRIORS_MODE == "off":
        return False
    if PRIORS_MODE == "wilson":
        return _wilson_blocked_from_cell("hf_hour", str(utc_hour(now)))
    h = utc_hour(now)
    if h not in HF_AVOID_HOURS:
        return False
    return float(score) < float(min_score) * 1.35


def load_stats_note() -> str:
    mode = PRIORS_MODE
    try:
        o = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        return f"stats@{o.get('generated_at')}|priors={mode}"
    except (OSError, json.JSONDecodeError, TypeError):
        return f"builtin_priors|priors={mode}"


def stats_age_hours(now: float | None = None) -> float | None:
    import time

    try:
        o = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        gen = float(o.get("generated_at") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if gen <= 0:
        return None
    return (float(now if now is not None else time.time()) - gen) / 3600.0


def stats_stale(
    now: float | None = None,
    *,
    max_age_h: float | None = None,
) -> bool:
    limit = max_age_h
    if limit is None:
        limit = float(os.environ.get("SN89_FLEET_STATS_MAX_AGE_H", "48"))
    age = stats_age_hours(now)
    if age is None:
        return True
    return age > float(limit)
