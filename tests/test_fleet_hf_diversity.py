"""HF diversity guard for the rabi fleet — uses sn89_signals.hf.hf_diversity."""
from __future__ import annotations

import math
import time

import pytest

from sn89_signals import hf as hf_mod

# scripts/ on path via conftest or sys.path
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
    """Build (t0_ms, pair, dir, hz) rows matching board by_pair counts."""
    now = now if now is not None else time.time()
    t0 = int((now - 3600) * 1000)
    out: list[tuple] = []
    i = 0
    for pair, (longs, shorts) in by_pair.items():
        for _ in range(longs):
            out.append((t0 + i, pair, "LONG", horizon_s))
            i += 1
        for _ in range(shorts):
            out.append((t0 + i, pair, "SHORT", horizon_s))
            i += 1
    return out


# Board snapshot 2026-10-16 ~ h1 (share≈0.156, floor≈0.272, n=32, breadth=1)
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
    # use mean ~55min like board for floor; rebuild with mixed hz via actual mean
    d = hf_mod.hf_diversity(subs, now)
    assert d["n"] == 32
    assert abs(d["share"] - 0.15625) < 1e-9
    # breadth 1 (all crypto) → narrow floor * horizon factor
    assert d["breadth"] == 1
    floor = hf_mod.hf_diversity_floor(d["breadth"], d["mean_horizon_s"])
    assert abs(d["floor"] - floor) < 1e-12
    assert d["applies"] is False  # n < 40


def test_guard_blocks_one_sided_add_near_min_n():
    now = time.time()
    # Pad h1 book to n=30 with one-sided noise so guard is active, still below floor.
    by = dict(H1_BY_PAIR)
    # already 32; strip to exactly synthesize a failing projected add
    subs = _subs_from_by_pair(by, horizon_s=1800.0, now=now)
    assert len(subs) >= 30
    # Adding another LONG on a long-only pair worsens / fails margin vs floor+0.05
    cand = {"pair": "BCHUSD", "direction": "LONG", "horizon_s": 1800}
    allow, cur, proj = g.diversity_guard_allows(
        subs, cand, now, margin=0.05, guard_min_n=30, enabled=True
    )
    assert cur["n"] >= 30
    assert proj["n"] == cur["n"] + 1
    assert allow is False
    assert proj["share"] < proj["floor"] + 0.05


def test_guard_allows_minority_repair():
    now = time.time()
    subs = _subs_from_by_pair(H1_BY_PAIR, horizon_s=1800.0, now=now)
    # SHORT on BCHUSD (currently 2/0) increases minority
    cand = {"pair": "BCHUSD", "direction": "SHORT", "horizon_s": 1800}
    allow, cur, proj = g.diversity_guard_allows(
        subs, cand, now, margin=0.05, guard_min_n=30, enabled=True
    )
    assert proj["share"] > cur["share"]
    # May still be below floor+margin, but better than worsening — check vs LONG
    cand_bad = {"pair": "BCHUSD", "direction": "LONG", "horizon_s": 1800}
    allow_bad, _, proj_bad = g.diversity_guard_allows(
        subs, cand_bad, now, margin=0.05, guard_min_n=30, enabled=True
    )
    assert proj["share"] >= proj_bad["share"]
    # With margin 0.05 and current ~0.156 vs floor~0.20, even repair may fail —
    # that is correct (wait rather than submit). Soften margin for this unit:
    allow_soft, _, proj_soft = g.diversity_guard_allows(
        subs, cand, now, margin=0.0, guard_min_n=30, enabled=True
    )
    # share still below floor typically — then allow_soft may still be False
    if proj_soft["share"] >= proj_soft["floor"]:
        assert allow_soft is True
    else:
        assert allow_soft is False


def test_guard_disabled_always_allows():
    now = time.time()
    subs = _subs_from_by_pair(H1_BY_PAIR, horizon_s=1800.0, now=now)
    cand = {"pair": "BCHUSD", "direction": "LONG", "horizon_s": 1800}
    allow, _, _ = g.diversity_guard_allows(
        subs, cand, now, margin=0.05, guard_min_n=30, enabled=False
    )
    assert allow is True


def test_guard_inactive_below_min_n():
    now = time.time()
    subs = _subs_from_by_pair({"BTCUSD": (5, 0)}, horizon_s=1800.0, now=now)
    cand = {"pair": "BTCUSD", "direction": "LONG", "horizon_s": 1800}
    allow, _, proj = g.diversity_guard_allows(
        subs, cand, now, margin=0.05, guard_min_n=30, enabled=True
    )
    assert proj["n"] < 30
    assert allow is True


def test_minority_edge_tol():
    assert g.minority_edge_ok(0.05, min_edge=0.03, tol=0.02) is True
    assert g.minority_edge_ok(-0.01, min_edge=0.03, tol=0.02) is True
    assert g.minority_edge_ok(-0.03, min_edge=0.03, tol=0.02) is False


def test_floor_matches_hf_diversity_floor_helper():
    assert hf_mod.hf_diversity_floor(1, 1800.0) == pytest.approx(
        hf_mod.HF_DIVERSITY_FLOOR_NARROW
    )
    # longer horizon raises floor
    f30 = hf_mod.hf_diversity_floor(1, 1800.0)
    f120 = hf_mod.hf_diversity_floor(1, 7200.0)
    assert f120 > f30
    assert f120 <= hf_mod.HF_DIVERSITY_FLOOR_CEIL
