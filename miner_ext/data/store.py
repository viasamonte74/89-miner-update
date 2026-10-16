"""Tick store: load (pair, [t0, end]) from archived raw windows.

Mirrors `hf_grade._ticks_for` layout so injected ticks match the grader feed.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from .paths import RAW_WINDOWS

WINDOW_MS = 180_000


def list_archived_windows() -> list[int]:
    if not os.path.isdir(RAW_WINDOWS):
        return []
    out = []
    for name in os.listdir(RAW_WINDOWS):
        if name.isdigit() and os.path.isfile(
            os.path.join(RAW_WINDOWS, name, "ticks.jsonl")
        ):
            out.append(int(name))
    return sorted(out)


@lru_cache(maxsize=8192)
def _window_pair_ticks(w: int, pair: str) -> tuple[dict, ...]:
    path = os.path.join(RAW_WINDOWS, str(w), "ticks.jsonl")
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("a") == pair:
                    rows.append(d)
    except OSError:
        return tuple()
    return tuple(rows)


def clear_tick_cache() -> None:
    _window_pair_ticks.cache_clear()


def load_ticks(
    pair: str, t0_ms: int, end_ms: int, *, leading_window: bool = True
) -> tuple[list[dict], list[int]]:
    """Return (ticks, missing_windows) covering entry at t0 through end_ms.

    Starts one window before t0's window (same as hf_grade._ticks_for) so the
    last pre-t0 tick exists for entry.
    """
    available = set(list_archived_windows())
    out: list[dict] = []
    missing: list[int] = []
    start_w = (t0_ms // WINDOW_MS - (1 if leading_window else 0)) * WINDOW_MS
    w = start_w
    while w <= end_ms:
        if w not in available:
            missing.append(w)
        else:
            for d in _window_pair_ticks(w, pair):
                tm = int(d["t"])
                if tm <= end_ms:
                    out.append(dict(d))
        w += WINDOW_MS
    out.sort(key=lambda d: int(d["t"]))
    return out, missing
