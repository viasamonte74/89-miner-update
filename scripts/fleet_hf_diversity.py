"""HF diversity bookkeeping for the rabi fleet (calls sn89_signals.hf, no re-derive)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from sn89_signals import hf as hf_mod

STATE_DIR = Path(os.environ.get("SN89_STATE_DIR", os.path.expanduser("~/.sn89")))


def receipts_path(hotkey: str, state_dir: Path | None = None) -> Path:
    root = state_dir or STATE_DIR
    return root / f"hf_receipts_{hotkey}.jsonl"


def load_subs_from_receipts(path: Path) -> list[tuple]:
    """Return `(t0_ms, pair, direction, horizon_s)` from local ingest receipts."""
    out: list[tuple] = []
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        sub = o.get("submit") or {}
        rec = o.get("receipt") or {}
        pay = sub.get("payload")
        if isinstance(pay, str):
            try:
                pay = json.loads(pay)
            except json.JSONDecodeError:
                continue
        if not isinstance(pay, dict):
            continue
        pair = str(pay.get("trade_pair") or pay.get("pair") or "").upper()
        direction = str(pay.get("direction") or "").upper()
        if not pair or direction not in {"LONG", "SHORT"}:
            continue
        t0 = rec.get("grid_t0_ms")
        if t0 is None:
            t0 = sub.get("ts_miner")
        if t0 is None:
            continue
        hz = pay.get("horizon_s")
        try:
            hz_f = float(hz) if hz is not None else None
        except (TypeError, ValueError):
            hz_f = None
        row: tuple = (int(t0), pair, direction)
        if hz_f:
            row = (int(t0), pair, direction, hz_f)
        out.append(row)
    return out


def load_subs_from_submit_log(path: Path) -> list[tuple]:
    """Fallback: `submit_ok` lines from hf_auto_loop JSONL."""
    out: list[tuple] = []
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        pair = str(o.get("pair") or "").upper()
        direction = str(o.get("direction") or "").upper()
        if not pair or direction not in {"LONG", "SHORT"}:
            continue
        ts = o.get("ts")
        if ts is None:
            continue
        # log ts is unix seconds; diversity wants ms
        t0_ms = int(float(ts) * 1000.0)
        hz = o.get("horizon_s")
        try:
            hz_f = float(hz) if hz is not None else None
        except (TypeError, ValueError):
            hz_f = None
        row: tuple = (t0_ms, pair, direction)
        if hz_f:
            row = (t0_ms, pair, direction, hz_f)
        out.append(row)
    return out


def load_diversity_subs(
    hotkey: str,
    *,
    log_path: Path | None = None,
    state_dir: Path | None = None,
) -> list[tuple]:
    """Prefer receipts; merge submit_ok log for any gaps (by t0_ms+pair+dir)."""
    rec = load_subs_from_receipts(receipts_path(hotkey, state_dir))
    seen = {(r[0], r[1], r[2]) for r in rec}
    if log_path is not None:
        for r in load_subs_from_submit_log(log_path):
            key = (r[0], r[1], r[2])
            if key not in seen:
                rec.append(r)
                seen.add(key)
    return rec


def project_diversity(
    subs: Iterable[tuple],
    candidate: dict[str, Any],
    now: float,
) -> dict:
    """`hf_diversity` after appending one candidate submission at `now`."""
    t0_ms = int(float(now) * 1000.0)
    pair = str(candidate["pair"]).upper()
    direction = str(candidate["direction"]).upper()
    hz = candidate.get("horizon_s")
    row: tuple = (t0_ms, pair, direction)
    if hz:
        row = (t0_ms, pair, direction, float(hz))
    merged = list(subs) + [row]
    return hf_mod.hf_diversity(merged, now)


def diversity_guard_allows(
    subs: list[tuple],
    candidate: dict[str, Any],
    now: float,
    *,
    margin: float,
    guard_min_n: int,
    enabled: bool = True,
) -> tuple[bool, dict, dict]:
    """Return (allow, current_div, projected_div).

    Once projected n ≥ guard_min_n, require projected share ≥ floor + margin.
    When the official gate does not yet apply (n < HF_DIVERSITY_MIN_SUBS), we
    still enforce the margin buffer so we do not walk into a failing book.
    """
    current = hf_mod.hf_diversity(subs, now)
    projected = project_diversity(subs, candidate, now)
    if not enabled:
        return True, current, projected
    n = int(projected.get("n") or 0)
    if n < int(guard_min_n):
        return True, current, projected
    floor = float(projected.get("floor") or 0.0)
    share = float(projected.get("share") or 0.0)
    target = floor + float(margin)
    return share + 1e-12 >= target, current, projected


def minority_edge_ok(edge: float, *, min_edge: float, tol: float) -> bool:
    """Trend side needs edge ≥ min_edge; minority relief allows down to -tol."""
    if edge >= float(min_edge):
        return True
    return edge >= -float(tol)
