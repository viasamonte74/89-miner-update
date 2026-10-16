"""Summarize graded calls with repo scoring helpers (Wilson, tier, QUALIFIED)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config, scoring
from sn89_signals.grader import Grade, WON, LOST, WASHED, PENDING


@dataclass
class TradeRow:
    pair: str
    direction: str
    t0_unix: float
    status: str
    exit_reason: str | None
    outcome_bps: float | None


def from_grades(
    pairs: list[str],
    directions: list[str],
    t0s: list[float],
    grades: list[Grade],
) -> list[TradeRow]:
    rows = []
    for pair, direction, t0, g in zip(pairs, directions, t0s, grades):
        rows.append(
            TradeRow(
                pair=pair,
                direction=direction,
                t0_unix=t0,
                status=g.status,
                exit_reason=g.exit_reason,
                outcome_bps=g.outcome_bps,
            )
        )
    return rows


def summarize(rows: list[TradeRow], *, now_unix: float | None = None) -> dict:
    won = sum(1 for r in rows if r.status == WON)
    lost = sum(1 for r in rows if r.status == LOST)
    wash = sum(1 for r in rows if r.status == WASHED)
    pending = sum(1 for r in rows if r.status == PENDING)
    decisive = won + lost
    hit = (won / decisive) if decisive else 0.0
    lb = scoring.confident_edge(won, decisive) if decisive else 0.0
    shrunk = scoring.shrunk_hit_rate(won, decisive) if decisive else 0.5
    qualified = scoring.is_qualified(won, decisive)
    tier = scoring.tier_multiplier(won, decisive, t0_unix=now_unix or 1e12)

    # decayed emission-style tally (single hotkey toy): post-warmup wins only
    # if first_seen unknown, treat all as post-warmup for the report
    now = now_unix if now_unix is not None else (max((r.t0_unix for r in rows), default=0.0))
    decisive_tuples = [
        (r.t0_unix, r.status == WON, False)
        for r in rows
        if r.status in (WON, LOST)
    ]
    first_seen = min((r.t0_unix for r in rows), default=now) - config.IMMUNITY_S
    rep_w, rep_d, tw_all, tw_orig, copies, tdec = scoring.score_inputs(
        decisive_tuples, first_seen, now
    )

    return {
        "n": len(rows),
        "won": won,
        "lost": lost,
        "washed": wash,
        "pending": pending,
        "decisive": decisive,
        "hit_rate": hit,
        "wilson_lb": lb,
        "shrunk_hit_rate": shrunk,
        "qualified": qualified,
        "tier_multiplier": tier,
        "rep_wins": rep_w,
        "rep_decisive": rep_d,
        "trailing_wins_post_warmup": tw_all,
        "qualify_min_decisive": config.QUALIFY_MIN_DECISIVE,
        "qualify_lb_floor": config.QUALIFY_LB_FLOOR,
    }
