"""Unit tests for HF fade-spike direction helper."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import hf_fade_spike as fs  # noqa: E402


def _series(n: int = 120, *, spike_ret: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Flat 1m closes with an optional last-window log move."""
    t0 = 1_700_000_000_000
    t_ms = np.arange(n, dtype=np.int64) * 60_000 + t0
    close = np.full(n, 100.0, dtype=np.float64)
    # mild noise so σ > 0
    rng = np.random.default_rng(0)
    close *= np.exp(np.cumsum(rng.normal(0, 0.0005, size=n)))
    if spike_ret != 0.0:
        close[-1] = float(close[-6]) * math.exp(spike_ret)
    return t_ms, close


def test_no_spike_returns_none():
    t_ms, close = _series(spike_ret=0.0)
    assert fs.fade_direction(t_ms, close, int(t_ms[-1]), min_abs_z=2.0) is None


def test_up_spike_fades_short():
    t_ms, close = _series(spike_ret=0.05)  # large vs 0.05% noise
    out = fs.fade_direction(
        t_ms, close, int(t_ms[-1]), window_min=5, min_abs_z=2.0
    )
    assert out is not None
    assert out["direction"] == "SHORT"
    assert out["z"] > 2.0


def test_down_spike_fades_long():
    t_ms, close = _series(spike_ret=-0.05)
    out = fs.fade_direction(
        t_ms, close, int(t_ms[-1]), window_min=5, min_abs_z=2.0
    )
    assert out is not None
    assert out["direction"] == "LONG"
    assert out["z"] < -2.0


def test_threshold_boundary_spike():
    """Just-above / just-below min_abs_z at a controlled σ."""
    t0 = 1_700_000_000_000
    n = 90
    t_ms = np.arange(n, dtype=np.int64) * 60_000 + t0
    # Constant 1m log-return σ ≈ 0.001 so denom = 0.001 * sqrt(5)
    close = np.full(n, 100.0, dtype=np.float64)
    for i in range(1, n):
        close[i] = close[i - 1] * math.exp(0.001 if i % 2 == 0 else -0.001)
    # Rebuild with fixed rets so std is known: alternate ±r
    close = np.empty(n, dtype=np.float64)
    close[0] = 100.0
    r = 0.001
    for i in range(1, n):
        close[i] = close[i - 1] * math.exp(r if (i % 2) else -r)
    sig = float(np.std(np.diff(np.log(close[-61:])), ddof=1))
    denom = sig * math.sqrt(5.0)
    # Place exact z at tip vs 5m-ago
    z_target = 2.05
    close = close.copy()
    close[-1] = float(close[-6]) * math.exp(z_target * denom)
    out = fs.fade_direction(t_ms, close, int(t_ms[-1]), window_min=5, min_abs_z=2.0)
    assert out is not None
    assert out["abs_z"] >= 2.0
    # Below threshold
    close2 = close.copy()
    close2[-1] = float(close2[-6]) * math.exp(1.5 * denom)
    assert fs.fade_direction(t_ms, close2, int(t_ms[-1]), window_min=5, min_abs_z=2.0) is None


def test_near_dup_dedupe_key_buckets():
    import fleet_hf_diversity as g

    a = (1_000_000, "SUIUSD", "LONG", 1800.0)
    b = (1_001_500, "SUIUSD", "LONG", 1800.0)  # +1.5s → same 2s bucket
    assert g._dedupe_key(a) == g._dedupe_key(b)
    c = (1_005_000, "SUIUSD", "LONG", 1800.0)  # +5s → different bucket
    assert g._dedupe_key(a) != g._dedupe_key(c)
    assert g._near_dup(a, (1_004_000, "SUIUSD", "LONG", 1800.0))


def test_wall_clock_spike_with_live_hist_gap():
    """Dense 1m hist covering lookback+window (no archive bridge) → wall-clock z."""
    now = 1_700_000_000_000 + 200 * 60_000
    n = 90
    t_ms = np.asarray([now - (n - 1 - i) * 60_000 for i in range(n)], dtype=np.int64)
    close = np.full(n, 100.0, dtype=np.float64)
    rng = np.random.default_rng(1)
    close *= np.exp(np.cumsum(rng.normal(0, 0.0004, size=n)))
    close[-1] = float(close[-6]) * math.exp(0.04)
    out = fs.fade_direction(t_ms, close, now, window_min=5, min_abs_z=2.0)
    assert out is not None
    assert out["direction"] == "SHORT"
    assert out.get("wall_clock") is True


def test_stale_bridge_no_wall_clock_returns_none():
    """Archive tip + live tip 15m later with no hist → no wall-clock anchor → None."""
    t0 = 1_700_000_000_000
    n = 90
    t_bars = np.arange(n, dtype=np.int64) * 60_000 + t0
    close = np.full(n, 100.0, dtype=np.float64)
    rng = np.random.default_rng(2)
    close *= np.exp(np.cumsum(rng.normal(0, 0.0004, size=n)))
    # Only append a live tip 15 minutes later (the old bridge pattern)
    now = int(t_bars[-1] + 15 * 60_000)
    t_ms = np.append(t_bars, now)
    c = np.append(close, float(close[-1]) * math.exp(0.05))
    assert fs.fade_direction(t_ms, c, now, window_min=5, min_abs_z=1.75) is None


def test_append_live_does_not_bridge_gaps():
    import hf_live_quotes as lq

    t0 = 1_700_000_000_000
    t = np.array([t0], dtype=np.int64)
    c = np.array([100.0], dtype=np.float64)
    qt = t0 + 11 * 60_000
    t2, c2 = lq.append_live_to_series(t, c, {"t_ms": qt, "price": 101.0})
    assert int(t2[-1]) == qt
    assert float(c2[-1]) == 101.0
    assert len(t2) == 2  # tip only — no forward-filled minutes


def test_parse_tick_ms_requires_timestamp():
    import hf_live_quotes as lq

    assert lq._parse_tick_ms({"price": 1.0}) is None
    assert lq._parse_tick_ms({"price": 1.0, "t": 1_700_000_000}) == 1_700_000_000_000
    assert lq._parse_tick_ms({"price": 1.0, "t_ms": 1_700_000_000_000}) == 1_700_000_000_000


def test_parse_tick_ms_divides_microseconds():
    import hf_live_quotes as lq

    # µs epoch → ms
    assert lq._parse_tick_ms({"t": 1_700_000_000_000_000}) == 1_700_000_000_000


def test_future_timestamp_not_fresh():
    import time

    import hf_live_quotes as lq

    now = time.time()
    future_ms = int((now + 120) * 1000)  # 2 minutes ahead
    q = {"t_ms": future_ms, "price": 1.0}
    assert lq.quote_is_fresh(q, now=now) is False
    # small skew still OK
    skew_ms = int((now + 5) * 1000)
    assert lq.quote_is_fresh({"t_ms": skew_ms, "price": 1.0}, now=now) is True
