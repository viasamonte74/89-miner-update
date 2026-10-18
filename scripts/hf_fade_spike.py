"""HF fade-spike direction: fade a ≥Zσ move over the last W minutes.

Backtest (1m bars, 30m HF bands): fade 5m/10m ≥2σ ~51.8–52.0% hit rate vs
~49.3% for following the last 2h trend. Env-gated from hf_auto_loop.

Requires a wall-clock anchor within max_anchor_lag_s of the window start.
Bar-index fallback across archive gaps is disabled (invents fake spikes).
"""
from __future__ import annotations

import math
import os
from typing import Any

import numpy as np

# Reject when the window-start sample is farther than this from target0.
MAX_ANCHOR_LAG_S = float(os.environ.get("SN89_HF_FADE_MAX_ANCHOR_LAG_S", "120"))


def _idx_at_or_before(t_ms: np.ndarray, decision_ms: int) -> int:
    return int(np.searchsorted(t_ms, decision_ms, side="right") - 1)


def spike_z(
    t_ms: np.ndarray,
    close: np.ndarray,
    decision_ms: int,
    *,
    window_min: int = 5,
    sig_lookback_min: int = 60,
    max_anchor_lag_s: float | None = None,
) -> dict[str, float] | None:
    """Return spike stats or None if series too short / invalid / stale.

    z = log-return over wall-clock ``window_min`` / (σ_1m * sqrt(window_min)),
    where σ_1m is the sample std of 1-minute log returns over the lookback
    ending at the window start (avoids mixing the spike into σ).
    """
    t_ms = np.asarray(t_ms, dtype=np.int64)
    close = np.asarray(close, dtype=np.float64)
    decision_ms = int(decision_ms)
    lag_lim = (
        float(MAX_ANCHOR_LAG_S)
        if max_anchor_lag_s is None
        else float(max_anchor_lag_s)
    )
    i1 = _idx_at_or_before(t_ms, decision_ms)
    if i1 < 0:
        return None
    target0 = decision_ms - int(window_min) * 60_000
    i0 = _idx_at_or_before(t_ms, target0)
    # Fail closed: require a real wall-clock sample near the window start.
    # Bar-index fallback across a 10–30m archive gap invents fake ≥1.75σ spikes.
    if i0 < 0:
        return None
    anchor_lag_s = float((int(t_ms[i0]) - target0) / 1000.0)
    if abs(anchor_lag_s) > lag_lim:
        return None
    c0 = float(close[i0])
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
        "wall_clock": True,
    }


def fade_direction(
    t_ms: Any,
    close: Any,
    decision_ms: int,
    *,
    window_min: int = 5,
    sig_lookback_min: int = 60,
    min_abs_z: float = 2.0,
    max_anchor_lag_s: float | None = None,
) -> dict[str, Any] | None:
    """If |z| ≥ min_abs_z, return fade side (SHORT after up-spike, LONG after down)."""
    stats = spike_z(
        t_ms,
        close,
        decision_ms,
        window_min=window_min,
        sig_lookback_min=sig_lookback_min,
        max_anchor_lag_s=max_anchor_lag_s,
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
        "wall_clock": bool(stats.get("wall_clock")),
    }
