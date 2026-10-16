"""Fleet-learned priors from our own graded LF/HF history.

Source of truth for refresh: /workspace/sn89_data/fleet_pair_stats.json
(regenerate via scripts/refresh_fleet_pair_stats.sh).

Soft multipliers bias ranking. Hard denylists / hour blocks skip toxic setups
until stats recover. Retuned 2026-10-15 from refreshed book
(LF 46 graded / 31 decisive, HF 93 / 89 decisive).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

STATS_PATH = Path(
    os.environ.get("SN89_FLEET_STATS", "/workspace/sn89_data/fleet_pair_stats.json")
)

# --- LF: pair score mult overlays ---
# Confident: SUI 3W/1L; AVAX 0/4; BTC 9/10 wash; NEAR/DOT weak (1/2).
LF_PAIR_MULT = {
    "AVAXUSD": 0.35,
    "BTCUSD": 0.12,
    "ETHUSD": 0.25,
    "SOLUSD": 0.55,
    "KPEPEUSD": 0.70,
    "NEARUSD": 0.85,  # 1W/2L + washes
    "DOTUSD": 0.90,  # 1W/2L
    "TAOUSD": 1.05,  # 4W/4L coin-flip — mild only
    "ZROUSD": 1.10,
    "ALGOUSD": 0.95,
    "LTCUSD": 1.10,
    "SUIUSD": 1.35,  # 3W/1L best LF pair
    "BCHUSD": 1.15,
}

# Hard LF pair ban (still clear in refreshed book).
LF_PAIR_DENY = frozenset(
    {
        "AVAXUSD",  # 0W/4L decisive
        "BTCUSD",  # 9/10 washes
        "ETHUSD",  # majors wash / no edge
        "NEARUSD",  # 1W/2L + washes — park until recovered
        "DOTUSD",  # 1W/2L
    }
)

# UTC hour soft mult for LF
LF_HOUR_MULT = {
    1: 1.35,  # 5W/2L — strongest LF hour
    8: 1.15,
    9: 1.20,  # 2W/1L
    19: 1.30,
    20: 1.25,
    3: 0.65,
    7: 0.55,
    10: 0.65,
    11: 0.50,
    12: 0.60,
    16: 0.70,
    17: 0.50,  # wash-heavy
    18: 0.85,
}

# Hard LF hour skip (≥2 decisive, 0% HR). Soft-avoid needs score gate.
LF_HARD_AVOID_HOURS = frozenset({7, 11})
LF_SOFT_AVOID_HOURS = frozenset({3, 10, 12, 16, 17})
LF_SOFT_AVOID_SCORE_MULT = 1.40

# --- HF: pair×direction overlays ---
HF_PAIR_DIR_MULT = {
    # Strong (dec≥3 or perfect n≥2)
    ("ADAUSD", "SHORT"): 1.45,  # 4/0
    ("SUIUSD", "SHORT"): 1.40,  # 4/1
    ("ALGOUSD", "SHORT"): 1.35,  # 2/0
    ("WLDUSD", "LONG"): 1.30,  # 2/0
    ("XRPUSD", "SHORT"): 1.25,  # 2/1
    ("NEARUSD", "SHORT"): 1.20,  # 2/1
    ("BCHUSD", "LONG"): 1.20,  # 2/1
    ("KPEPEUSD", "LONG"): 1.20,  # 2/1
    ("LINKUSD", "LONG"): 1.20,  # 2/1
    ("ALGOUSD", "LONG"): 1.10,
    ("SUIUSD", "LONG"): 1.00,  # 2/2 — neutral
    # Washed / coin-flip — no boost
    ("AVAXUSD", "SHORT"): 0.95,  # was 2/0, now 2/2
    ("AVAXUSD", "LONG"): 0.95,
    # Weak (soft down; hard-deny list below for worst)
    ("CRVUSD", "LONG"): 0.70,  # 2/3
    ("CRVUSD", "SHORT"): 0.60,  # 1/2
    ("WLDUSD", "SHORT"): 0.55,  # 1/2
    ("UNIUSD", "LONG"): 0.55,  # 1/2
    ("UNIUSD", "SHORT"): 0.55,
    ("DOTUSD", "SHORT"): 0.55,  # 1/2
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

# Hard HF pair×direction ban — 0% HR with ≥2 decisive, or pair 0/2.
HF_PAIR_DIR_DENY = frozenset(
    {
        ("LINKUSD", "SHORT"),  # 0/3
        ("TAOUSD", "SHORT"),  # 0/2
        ("TAOUSD", "LONG"),  # pair 0/2 TAO
        ("ZROUSD", "LONG"),  # pair 0/2
        ("ZROUSD", "SHORT"),
        ("ARBUSD", "LONG"),  # 0/2
        ("ARBUSD", "SHORT"),
        ("BNBUSD", "SHORT"),  # 0/2
    }
)

# HF hours — avoid 8/13/14 (0/3); soft-penalize 1/6/11/12
HF_AVOID_HOURS = {1, 6, 8, 11, 12, 13, 14}
HF_PREFER_HOURS = {0, 2, 3, 5, 9}
HF_HOUR_SCORE_MULT = {
    5: 1.25,  # 5W/0L
    3: 1.20,  # 5W/1L
    9: 1.15,
    2: 1.12,
    0: 1.08,
    4: 1.05,
    7: 1.05,
    10: 0.80,  # 6W/8L volume dump
    1: 0.55,  # 3W/5L
    6: 0.50,
    8: 0.40,
    11: 0.55,
    12: 0.55,
    13: 0.35,
    14: 0.35,
}


def utc_hour(now: float) -> int:
    return int((float(now) % 86_400) // 3600)


def lf_pair_mult(pair: str) -> float:
    return float(LF_PAIR_MULT.get(str(pair).upper(), 1.0))


def lf_pair_blocked(pair: str) -> bool:
    return str(pair).upper() in LF_PAIR_DENY


def lf_hour_mult(now: float) -> float:
    return float(LF_HOUR_MULT.get(utc_hour(now), 1.0))


def lf_hour_blocked(now: float, *, score: float, score_floor: float) -> bool:
    """True → do not submit LF in this UTC hour (hard or soft gate)."""
    h = utc_hour(now)
    if h in LF_HARD_AVOID_HOURS:
        return True
    if h in LF_SOFT_AVOID_HOURS:
        return float(score) < float(score_floor) * LF_SOFT_AVOID_SCORE_MULT
    return False


def hf_pair_dir_mult(pair: str, direction: str) -> float:
    return float(
        HF_PAIR_DIR_MULT.get((str(pair).upper(), str(direction).upper()), 1.0)
    )


def hf_pair_dir_blocked(pair: str, direction: str) -> bool:
    return (str(pair).upper(), str(direction).upper()) in HF_PAIR_DIR_DENY


def hf_hour_mult(now: float) -> float:
    return float(HF_HOUR_SCORE_MULT.get(utc_hour(now), 1.0))


def hf_hour_blocked(now: float, *, score: float, min_score: float) -> bool:
    """Block weak UTC hours unless score is clearly above the floor."""
    h = utc_hour(now)
    if h not in HF_AVOID_HOURS:
        return False
    return float(score) < float(min_score) * 1.35


def load_stats_note() -> str:
    try:
        o = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        return f"stats@{o.get('generated_at')}"
    except (OSError, json.JSONDecodeError, TypeError):
        return "builtin_priors"
