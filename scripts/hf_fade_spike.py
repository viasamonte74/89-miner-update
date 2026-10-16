"""HF fade-spike direction: fade a ≥Zσ move over the last W minutes.

Backtest (1m bars, 30m HF bands): fade 5m/10m ≥2σ ~51.8–52.0% hit rate vs
~49.3% for following the last 2h trend. Env-gated from hf_auto_loop.
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

    z = log-return over ``window_min`` / (σ_1m * sqrt(window_min)), where σ_1m
    is the sample std of 1-minute log returns over ``sig_lookback_min``.
    """
    t_ms = np.asarray(t_ms, dtype=np.int64)
    close = np.asarray(close, dtype=np.float64)
    i = _idx_at_or_before(t_ms, decision_ms)
    need = max(int(window_min), int(sig_lookback_min)) + 1
    if i < need:
        return None
    c1 = float(close[i])
    c0 = float(close[i - int(window_min)])
    if c0 <= 0 or c1 <= 0 or not math.isfinite(c0) or not math.isfinite(c1):
        return None
    ret = math.log(c1 / c0)
    sl = close[i - int(sig_lookback_min) : i + 1]
    if np.any(~np.isfinite(sl)) or np.any(sl <= 0):
        return None
    rets = np.diff(np.log(sl))
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
    }
