"""Backtester must match grader._grade_touch_ticks / grade() on injected ticks."""
from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config, grader
from sn89_signals.grader import WON, LOST, WASHED, PENDING
from sn89_signals.schema import Signal

from miner_ext.backtest.grade import Call, grade_call, make_signal
from miner_ext.backtest.report import from_grades, summarize
from miner_ext.backtest.rules import ScheduledCall, filter_lf_schedule

T0 = (config.TOUCH_TICKS_FROM + 4 * 86_400) * 1000
HK = "5" + "F" * 47
HORIZON_MS = 8 * 3_600_000


def _tick(t, p, pair="BTCUSD"):
    return {"a": pair, "b": p - 1, "k": p + 1, "p": p, "t": t, "s": "test"}


def _direct_grade(direction, ticks, now_ms=None):
    sig = Signal(
        trade_pair="BTCUSD",
        direction=direction,
        tp_bps=105,
        sl_bps=105,
        ts_miner=T0,
        hotkey=HK,
        asset_class="crypto",
        horizon_h=8,
    )
    now = now_ms if now_ms is not None else T0 + HORIZON_MS + 1
    return grader.grade(sig, T0, now, ticks=ticks)


def test_touch_ticks_rule_in_force():
    assert config.grading_rule_as_of(T0 / 1000.0) == "touch_ticks"


def test_long_tp_win_matches_grader():
    entry = 64_000.0
    tp = entry * (1 + 105 / 10_000)
    ticks = [
        _tick(T0 - 500, entry),
        _tick(T0 + 1_000, tp),
        _tick(T0 + 2_000, tp),  # 2 touches
    ]
    direct = _direct_grade("LONG", ticks)
    ours = grade_call(
        Call("BTCUSD", "LONG", T0, tp_bps=105, sl_bps=105, horizon_h=8),
        ticks=ticks,
        use_archive=False,
    )
    assert ours.status == direct.status == WON
    assert ours.exit_reason == direct.exit_reason == "tp_touch"
    assert ours.entry_price == pytest.approx(entry)


def test_long_sl_loss_matches_grader():
    entry = 64_000.0
    sl = entry * (1 - 105 / 10_000)
    ticks = [
        _tick(T0 - 500, entry),
        _tick(T0 + 1_000, sl),
        _tick(T0 + 2_000, sl),
    ]
    direct = _direct_grade("LONG", ticks)
    ours = grade_call(
        Call("BTCUSD", "LONG", T0, tp_bps=105, sl_bps=105, horizon_h=8),
        ticks=ticks,
        use_archive=False,
    )
    assert ours.status == direct.status == LOST


def test_single_touch_does_not_score():
    entry = 64_000.0
    tp = entry * (1 + 105 / 10_000)
    ticks = [
        _tick(T0 - 500, entry),
        _tick(T0 + 1_000, tp),  # only 1 touch
        _tick(T0 + 3_000, entry),
    ]
    direct = _direct_grade("LONG", ticks)
    ours = grade_call(
        Call("BTCUSD", "LONG", T0, tp_bps=105, sl_bps=105, horizon_h=8),
        ticks=ticks,
        use_archive=False,
    )
    assert ours.status == direct.status == WASHED


def test_short_win_matches():
    entry = 64_000.0
    tp = entry * (1 - 105 / 10_000)
    ticks = [
        _tick(T0 - 500, entry),
        _tick(T0 + 1_000, tp),
        _tick(T0 + 2_000, tp),
    ]
    direct = _direct_grade("SHORT", ticks)
    ours = grade_call(
        Call("BTCUSD", "SHORT", T0, tp_bps=105, sl_bps=105, horizon_h=8),
        ticks=ticks,
        use_archive=False,
    )
    assert ours.status == direct.status == WON


def test_no_entry_injected_stays_pending():
    ticks = [_tick(T0 + 60_000, 64_000.0)]
    ours = grade_call(
        Call("BTCUSD", "LONG", T0, tp_bps=105, sl_bps=105, horizon_h=8),
        ticks=ticks,
        now_ms=T0 + HORIZON_MS + 1,
        use_archive=False,
    )
    assert ours.status == PENDING
    assert ours.entry_price is None


def test_band_as_of_resolution():
    sig = make_signal(Call("BTCUSD", "LONG", T0))
    assert sig.tp_bps == sig.sl_bps
    bands = config.allowed_assets_as_of(T0 / 1000.0)
    assert sig.tp_bps == float(bands["BTCUSD"]["tp_bps"])


def test_random_parity_batch():
    """Many synthetic paths: wrapper == grader.grade."""
    import random

    rng = random.Random(89)
    entry = 50_000.0
    for i in range(200):
        direction = "LONG" if rng.random() < 0.5 else "SHORT"
        # random walk of mids
        ticks = [_tick(T0 - 1_000, entry)]
        px = entry
        for j in range(40):
            px *= 1.0 + rng.uniform(-0.004, 0.004)
            ticks.append(_tick(T0 + (j + 1) * 60_000, px))
        direct = _direct_grade(direction, ticks)
        ours = grade_call(
            Call("BTCUSD", direction, T0, tp_bps=105, sl_bps=105, horizon_h=8),
            ticks=ticks,
            use_archive=False,
        )
        assert ours.status == direct.status, (i, direction, ours, direct)
        assert ours.exit_reason == direct.exit_reason
        assert ours.outcome_bps == direct.outcome_bps


def test_lf_rules_cap_and_gap():
    base = float(config.TOUCH_TICKS_FROM + 10 * 86400)
    calls = [
        ScheduledCall("BTCUSD", "LONG", base),
        ScheduledCall("ETHUSD", "LONG", base + 600),  # too soon
        ScheduledCall("SOLUSD", "SHORT", base + 3600),
        ScheduledCall("XRPUSD", "LONG", base + 7200),
        ScheduledCall("DOGEUSD", "LONG", base + 10800),  # 4th same day → reject
    ]
    ok, bad = filter_lf_schedule(calls)
    assert len(ok) == 3
    assert any(r == "min_spacing" for _, r in bad)
    assert any(r == "daily_quota" for _, r in bad)


def test_summarize_wilson_qualify():
    # fabricate 6 wins / 2 losses → QUALIFIED at n=8
    grades = []
    for i in range(6):
        grades.append(
            type("G", (), {"status": WON, "exit_reason": "tp_touch", "outcome_bps": 105.0})()
        )
    for i in range(2):
        grades.append(
            type("G", (), {"status": LOST, "exit_reason": "sl_touch", "outcome_bps": -105.0})()
        )
    rows = from_grades(
        ["BTCUSD"] * 8,
        ["LONG"] * 8,
        [T0 / 1000.0 + i * 86400 for i in range(8)],
        grades,
    )
    s = summarize(rows, now_unix=T0 / 1000.0 + 30 * 86400)
    assert s["won"] == 6 and s["lost"] == 2
    assert s["qualified"] is True
    assert s["wilson_lb"] >= config.QUALIFY_LB_FLOOR
