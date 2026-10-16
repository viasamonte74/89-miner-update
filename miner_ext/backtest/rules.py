"""LF submission constraints for simulated schedules (mirror miner local limits)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config


@dataclass
class ScheduledCall:
    pair: str
    direction: str
    t0_unix: float


def filter_lf_schedule(
    calls: list[ScheduledCall],
) -> tuple[list[ScheduledCall], list[tuple[ScheduledCall, str]]]:
    """Keep calls that respect as-of daily cap + min gap. Same-hotkey simulation.

    Returns (accepted, rejected_with_reason). Does not model opposite-direction
    LF voids (README allows long-then-short); HF open-gate is out of scope here.
    """
    accepted: list[ScheduledCall] = []
    rejected: list[tuple[ScheduledCall, str]] = []
    times: list[float] = []

    for c in sorted(calls, key=lambda x: x.t0_unix):
        cap, gap = config.submission_rules_as_of(c.t0_unix)
        if gap and times:
            since = c.t0_unix - max(times)
            # consensus slack exists on chain; for sim use strict gap (conservative)
            if since < gap:
                rejected.append((c, "min_spacing"))
                continue
        day = int(c.t0_unix // 86_400)
        today = sum(1 for t in times if int(t // 86_400) == day)
        if today >= cap:
            rejected.append((c, "daily_quota"))
            continue
        accepted.append(c)
        times.append(c.t0_unix)
    return accepted, rejected
