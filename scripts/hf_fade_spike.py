"""HF fade-spike direction: fade a ≥Zσ move over the last W minutes.

Backtest (1m bars, 30m HF bands): fade 5m/10m ≥2σ ~51.8–52.0% hit rate vs
~49.3% for following the last 2h trend. Env-gated from hf_auto_loop.

Uses wall-clock windows so a live tip on stale parquet still measures the
last W minutes of price, not W bar indices across an archive gap.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def _idx_at_or_before(t_ms: np.ndarray, decision_ms: int) -> int:
    return int(np.searchsorted(t_ms, decision_ms, side="right") - 1)


def spike_z(
    t_ms: np.ndarray,
    close: np.ndarray,
    decision_ms: int,
    *,
    window_min: int = 5,
    sig_lookback_min: int = 60,
) -> dict[str, float] | None:
    """Return spike stats or None if series too short / invalid.

    z = log-return over wall-clock ``window_min`` / (σ_1m * sqrt(window_min)),
    where σ_1m is the sample std of 1-minute log returns over the lookback
    ending at the window start (avoids mixing the spike into σ).
    """
    t_ms = np.asarray(t_ms, dtype=np.int64)
    close = np.asarray(close, dtype=np.float64)
    decision_ms = int(decision_ms)
    i1 = _idx_at_or_before(t_ms, decision_ms)
    if i1 < 0:
        return None
    target0 = decision_ms - int(window_min) * 60_000
    i0 = _idx_at_or_before(t_ms, target0)
    wall = i0 >= 0 and abs(int(t_ms[i0]) - target0) <= 90_000
    if wall:
        c0 = float(close[i0])
        anchor_lag_s = float((int(t_ms[i0]) - target0) / 1000.0)
        sig_end = i0
    else:
        # Fallback: W bar-steps (needs dense 1m history — fine on parquet,
        # wrong across an unbridged archive gap).
        w = int(window_min)
        if i1 < w:
            return None
        i0 = i1 - w
        c0 = float(close[i0])
        anchor_lag_s = float((decision_ms - int(t_ms[i0])) / 1000.0 - window_min * 60.0)
        sig_end = i0
    c1 = float(close[i1])
    if c0 <= 0 or c1 <= 0 or not math.isfinite(c0) or not math.isfinite(c1):
        return None
    ret = math.log(c1 / c0)

    need = int(sig_lookback_min) + 1
    if sig_end < need:
        return None
    sl_t = t_ms[sig_end - need + 1 : sig_end + 1]
    sl_c = close[sig_end - need + 1 : sig_end + 1]
    kept = [float(sl_c[0])]
    last = int(sl_t[0])
    for j in range(1, len(sl_t)):
        dt = int(sl_t[j]) - last
        if 30_000 <= dt <= 90_000:
            kept.append(float(sl_c[j]))
            last = int(sl_t[j])
        elif dt > 90_000:
            kept = [float(sl_c[j])]
            last = int(sl_t[j])
    if len(kept) < 12:
        kept = [float(x) for x in sl_c]
    if len(kept) < 12:
        return None
    arr = np.asarray(kept, dtype=np.float64)
    if np.any(~np.isfinite(arr)) or np.any(arr <= 0):
        return None
    rets = np.diff(np.log(arr))
    if len(rets) < 10:
        return None
    sig = float(np.std(rets, ddof=1))
    if not math.isfinite(sig) or sig <= 1e-12:
        return None
    denom = sig * math.sqrt(float(window_min))
    if denom <= 0:
        return None
    z = ret / denom
    return {
        "z": float(z),
        "ret": float(ret),
        "sig_1m": float(sig),
        "window_min": float(window_min),
        "anchor_lag_s": float(anchor_lag_s),
        "wall_clock": bool(wall),
    }


def fade_direction(
    t_ms: Any,
    close: Any,
    decision_ms: int,
    *,
    window_min: int = 5,
    sig_lookback_min: int = 60,
    min_abs_z: float = 2.0,
) -> dict[str, Any] | None:
    """If |z| ≥ min_abs_z, return fade side (SHORT after up-spike, LONG after down)."""
    stats = spike_z(
        t_ms,
        close,
        decision_ms,
        window_min=window_min,
        sig_lookback_min=sig_lookback_min,
    )
    if stats is None:
        return None
    z = float(stats["z"])
    if abs(z) < float(min_abs_z):
        return None
    direction = "SHORT" if z > 0 else "LONG"
    return {
        "direction": direction,
        "z": z,
        "abs_z": abs(z),
        "ret": float(stats["ret"]),
        "sig_1m": float(stats["sig_1m"]),
        "window_min": int(window_min),
        "min_abs_z": float(min_abs_z),
        "anchor_lag_s": stats.get("anchor_lag_s"),
    }
