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


def test_near_dup_dedupe_key_buckets():
    import fleet_hf_diversity as g

    a = (1_000_000, "SUIUSD", "LONG", 1800.0)
    b = (1_001_500, "SUIUSD", "LONG", 1800.0)  # +1.5s → same 2s bucket
    assert g._dedupe_key(a) == g._dedupe_key(b)
    c = (1_005_000, "SUIUSD", "LONG", 1800.0)  # +5s → different bucket
    assert g._dedupe_key(a) != g._dedupe_key(c)
    assert g._near_dup(a, (1_004_000, "SUIUSD", "LONG", 1800.0))
