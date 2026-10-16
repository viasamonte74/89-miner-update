"""HF diversity guard for the rabi fleet — uses sn89_signals.hf.hf_diversity."""
from __future__ import annotations

import time

import pytest

from sn89_signals import hf as hf_mod

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import fleet_hf_diversity as g  # noqa: E402


def _subs_from_by_pair(
    by_pair: dict[str, tuple[int, int]],
    *,
    horizon_s: float = 1800.0,
    now: float | None = None,
) -> list[tuple]:
    now = now if now is not None else time.time()
    t0 = int((now - 3600) * 1000)
    out: list[tuple] = []
    i = 0
    for pair, (longs, shorts) in by_pair.items():
        for _ in range(longs):
            out.append((t0 + i, pair, "LONG", horizon_s, i + 1))
            i += 1
        for _ in range(shorts):
            out.append((t0 + i, pair, "SHORT", horizon_s, i + 1))
            i += 1
    return out


H1_BY_PAIR = {
    "ADAUSD": (0, 1),
    "ALGOUSD": (1, 3),
    "ARBUSD": (1, 1),
    "AVAXUSD": (1, 0),
    "BCHUSD": (2, 0),
    "BNBUSD": (0, 1),
    "CRVUSD": (2, 0),
    "ENAUSD": (1, 0),
    "ETHUSD": (1, 0),
    "HYPEUSD": (0, 1),
    "KPEPEUSD": (1, 0),
    "LINKUSD": (0, 2),
    "LTCUSD": (1, 0),
    "NEARUSD": (0, 4),
    "SUIUSD": (2, 4),
    "WLDUSD": (1, 1),
}


def test_h1_book_matches_hf_diversity_floor_and_share():
    now = time.time()
    subs = _subs_from_by_pair(H1_BY_PAIR, horizon_s=3319.0, now=now)
    d = hf_mod.hf_diversity(subs, now)
    assert d["n"] == 32
    assert abs(d["share"] - 0.15625) < 1e-9
    assert d["breadth"] == 1
    floor = hf_mod.hf_diversity_floor(d["breadth"], d["mean_horizon_s"])
    assert abs(d["floor"] - floor) < 1e-12


def test_improve_mode_allows_minority_step_blocks_worsening():
    now = time.time()
    subs = _subs_from_by_pair(H1_BY_PAIR, horizon_s=1800.0, now=now)
    good = {"pair": "BCHUSD", "direction": "SHORT", "horizon_s": 1800}
    bad = {"pair": "BCHUSD", "direction": "LONG", "horizon_s": 1800}
    allow_g, cur, proj_g = g.diversity_guard_allows(
        subs, good, now, margin=0.05, guard_min_n=30, enabled=True, mode="improve"
    )
    allow_b, _, proj_b = g.diversity_guard_allows(
        subs, bad, now, margin=0.05, guard_min_n=30, enabled=True, mode="improve"
    )
    assert proj_g["share"] > cur["share"]
    assert allow_g is True
    assert proj_b["share"] <= cur["share"]
    assert allow_b is False


def test_target_mode_still_blocks_partial_repair():
    now = time.time()
    subs = _subs_from_by_pair(H1_BY_PAIR, horizon_s=1800.0, now=now)
    cand = {"pair": "BCHUSD", "direction": "SHORT", "horizon_s": 1800}
    allow, _, proj = g.diversity_guard_allows(
        subs, cand, now, margin=0.05, guard_min_n=30, enabled=True, mode="target"
    )
    # One minority call cannot clear floor+0.05 from ~0.156
    assert proj["share"] < proj["floor"] + 0.05
    assert allow is False


def test_dedupe_by_seq():
    now = time.time()
    t0 = int(now * 1000)
    # Same seq from receipt-like and log-like rows
    a = (t0, "SUIUSD", "LONG", 1800.0, 42)
    b = (t0 + 5000, "SUIUSD", "LONG", 1800.0, 42)  # different local clock
    assert g._dedupe_key(a) == g._dedupe_key(b)


def test_is_minority_side():
    now = time.time()
    subs = _subs_from_by_pair({"BCHUSD": (2, 0)}, horizon_s=1800.0, now=now)
    assert g.is_minority_side(subs, "BCHUSD", "SHORT", now) is True
    assert g.is_minority_side(subs, "BCHUSD", "LONG", now) is False


def test_minority_edge_requires_minority_flag():
    assert g.minority_edge_ok(-0.01, min_edge=0.03, tol=0.02, is_minority=True)
    assert not g.minority_edge_ok(-0.01, min_edge=0.03, tol=0.02, is_minority=False)
    assert g.minority_edge_ok(0.05, min_edge=0.03, tol=0.02, is_minority=False)


def test_guard_disabled_always_allows():
    now = time.time()
    subs = _subs_from_by_pair(H1_BY_PAIR, horizon_s=1800.0, now=now)
    cand = {"pair": "BCHUSD", "direction": "LONG", "horizon_s": 1800}
    allow, _, _ = g.diversity_guard_allows(
        subs, cand, now, margin=0.05, guard_min_n=30, enabled=False
    )
    assert allow is True
