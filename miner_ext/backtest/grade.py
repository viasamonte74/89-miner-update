"""Grade simulated LF calls with the repo's own grader (no reimplementation)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Sequence

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config, grader
from sn89_signals.grader import Grade
from sn89_signals.schema import Signal

from miner_ext.data.store import load_ticks

HK_PLACEHOLDER = "5" + "A" * 47


@dataclass
class Call:
    pair: str
    direction: str  # LONG | SHORT
    t0_ms: int
    tp_bps: float | None = None
    sl_bps: float | None = None
    horizon_h: int | None = None


def _bands_at(t0_unix: float) -> dict:
    return config.allowed_assets_as_of(t0_unix)


def resolve_call(call: Call) -> Call:
    """Fill band + horizon from board in force at t0."""
    t0_unix = call.t0_ms / 1000.0
    bands = _bands_at(t0_unix)
    meta = bands.get(call.pair)
    if meta is None:
        raise KeyError(f"{call.pair} not on board at t0={t0_unix}")
    tp = call.tp_bps if call.tp_bps is not None else float(meta["tp_bps"])
    sl = call.sl_bps if call.sl_bps is not None else float(meta["sl_bps"])
    hz = call.horizon_h
    if hz is None:
        hz = config.horizon_h_for(call.pair, t0_unix)
    return Call(
        pair=call.pair,
        direction=call.direction.upper(),
        t0_ms=call.t0_ms,
        tp_bps=tp,
        sl_bps=sl,
        horizon_h=hz,
    )


def make_signal(call: Call) -> Signal:
    c = resolve_call(call)
    t0_unix = c.t0_ms / 1000.0
    asset_class = config.asset_class_for(c.pair, t0_unix)
    return Signal(
        trade_pair=c.pair,
        direction=c.direction,
        tp_bps=float(c.tp_bps),
        sl_bps=float(c.sl_bps),
        ts_miner=c.t0_ms,
        hotkey=HK_PLACEHOLDER,
        asset_class=asset_class,
        horizon_h=int(c.horizon_h),
    )


def grade_call(
    call: Call,
    *,
    ticks: Sequence[dict] | None = None,
    now_ms: int | None = None,
    use_archive: bool = True,
) -> Grade:
    """Grade one call via grader.grade → _grade_touch_ticks when rule is touch_ticks.

    Prefer injecting `ticks`. If None and use_archive, load from local raw windows.
    """
    c = resolve_call(call)
    sig = make_signal(c)
    horizon_ms = c.t0_ms + int(c.horizon_h) * 3_600_000
    if now_ms is None:
        now_ms = horizon_ms + 1  # force terminal grade when series complete

    inj = list(ticks) if ticks is not None else None
    if inj is None and use_archive:
        inj, missing = load_ticks(c.pair, c.t0_ms, horizon_ms)
        if missing:
            # Incomplete series: still grade what we have, but caller should
            # treat missing windows as non-final (mirrors abandon/pending logic).
            pass

    return grader.grade(sig, c.t0_ms, now_ms, ticks=inj)


def grade_many(calls: Sequence[Call], **kwargs) -> list[Grade]:
    return [grade_call(c, **kwargs) for c in calls]
