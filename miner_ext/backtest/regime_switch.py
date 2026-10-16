"""No-ML regime switcher: bull → always_LONG, bear → rule strategy.

Regime = sign of BTC log-return over a trailing lookback (causal at t0).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from miner_ext.backtest.baselines import (
    Candidate,
    _idx_at_or_before,
    select_daily_top_k,
)
from miner_ext.backtest.rules import ScheduledCall, filter_lf_schedule

BEAR_MODES = ("momentum", "first_passage", "momentum_short_only")


def btc_log_ret(
    btc_t_ms: np.ndarray,
    btc_c: np.ndarray,
    t0_unix: float,
    lookback_d: float = 8.0,
) -> float | None:
    """Causal BTC log return over lookback_d days ending at/before t0."""
    decision_ms = int(t0_unix * 1000)
    i = _idx_at_or_before(btc_t_ms, decision_ms)
    lag = int(lookback_d * 1440)
    if i < lag or btc_c[i] <= 0 or btc_c[i - lag] <= 0:
        return None
    return float(math.log(btc_c[i] / btc_c[i - lag]))


def day_is_bull(
    btc_t_ms: np.ndarray,
    btc_c: np.ndarray,
    day_id: int,
    lookback_d: float = 8.0,
) -> bool:
    """Regime for a UTC day: True=bull (incl. flat/unknown), False=bear."""
    t_ref = float(day_id * 86_400 + 12 * 3600)  # midday UTC, causal lookback
    ret = btc_log_ret(btc_t_ms, btc_c, t_ref, lookback_d=lookback_d)
    if ret is None:
        return True
    return ret >= 0.0


def _al_candidate(pair: str, t0: float) -> Candidate:
    jitter = (hash((pair, int(t0) // 86400)) & 0xFFFF) / 1e6
    return Candidate(pair, "LONG", t0, 1.0 + jitter, "al_bull")


def _bear_candidate(row, bear_mode: str) -> Candidate | None:
    """Build one candidate from a LONG-row feature snapshot."""
    t0 = float(row.decision_unix)
    pair = row.pair
    if bear_mode == "momentum":
        ret = float(row.ret_60)
        if not math.isfinite(ret) or abs(ret) < 1e-6:
            return None
        direction = "LONG" if ret > 0 else "SHORT"
        return Candidate(pair, direction, t0, abs(ret), "mom_bear")
    if bear_mode == "momentum_short_only":
        ret = float(row.ret_60)
        if not math.isfinite(ret) or ret >= 0:
            return None
        return Candidate(pair, "SHORT", t0, abs(ret), "mom_short_bear")
    if bear_mode == "first_passage":
        p = float(row.fp_p_long)
        if not math.isfinite(p):
            return None
        edge = abs(p - 0.5)
        if edge < 0.02:
            return None
        direction = "LONG" if p >= 0.5 else "SHORT"
        score = p if direction == "LONG" else (1.0 - p)
        return Candidate(pair, direction, t0, score, "fp_bear")
    raise ValueError(f"unknown bear_mode {bear_mode}")


def schedule_regime_switch(
    df_slice: pd.DataFrame,
    btc_t_ms: np.ndarray,
    btc_c: np.ndarray,
    *,
    lookback_d: float = 8.0,
    bear_mode: str = "momentum",
    k: int = 3,
    regime_override: dict[int, bool] | None = None,
) -> list[ScheduledCall]:
    """Per UTC-day: bull→always_LONG candidates; bear→rule candidates from features.

    `regime_override`: optional day_id → is_bull (for permutation nulls).
    Uses LONG rows only as the feature carrier (one row per pair×time).
    """
    if bear_mode not in BEAR_MODES:
        raise ValueError(f"bear_mode must be one of {BEAR_MODES}")

    long_df = df_slice[df_slice["direction"] == "LONG"]
    cands: list[Candidate] = []
    for day_id, g in long_df.groupby("day_id"):
        day_id = int(day_id)
        if regime_override is not None and day_id in regime_override:
            bull = regime_override[day_id]
        else:
            bull = day_is_bull(btc_t_ms, btc_c, day_id, lookback_d=lookback_d)
        seen = set()
        for row in g.itertuples(index=False):
            key = (row.pair, int(row.decision_unix))
            if key in seen:
                continue
            seen.add(key)
            if bull:
                cands.append(_al_candidate(row.pair, float(row.decision_unix)))
            else:
                c = _bear_candidate(row, bear_mode)
                if c is not None:
                    cands.append(c)

    sched = select_daily_top_k(cands, k=k, min_gap_s=3600.0)
    sched, _ = filter_lf_schedule(sched)
    return sched


def regime_day_map(
    df_slice: pd.DataFrame,
    btc_t_ms: np.ndarray,
    btc_c: np.ndarray,
    lookback_d: float = 8.0,
) -> dict[int, bool]:
    """day_id → is_bull for days present in df_slice."""
    out = {}
    for day_id in df_slice["day_id"].unique():
        d = int(day_id)
        out[d] = day_is_bull(btc_t_ms, btc_c, d, lookback_d=lookback_d)
    return out
