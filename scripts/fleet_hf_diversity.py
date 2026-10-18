"""HF diversity bookkeeping for the rabi fleet (calls sn89_signals.hf, no re-derive)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from sn89_signals import hf as hf_mod

STATE_DIR = Path(os.environ.get("SN89_STATE_DIR", os.path.expanduser("~/.sn89")))

# improve = allow if projected share >= current (repair path to floor)
# target = legacy one-step clear of floor+margin (too strict for deep holes)
GUARD_MODE = os.environ.get("SN89_HF_DIVERSITY_GUARD_MODE", "improve").strip().lower()


def receipts_path(hotkey: str, state_dir: Path | None = None) -> Path:
    root = state_dir or STATE_DIR
    return root / f"hf_receipts_{hotkey}.jsonl"


def _row_from_payload(
    pay: dict, t0: int, *, seq: int | None = None
) -> tuple | None:
    pair = str(pay.get("trade_pair") or pay.get("pair") or "").upper()
    direction = str(pay.get("direction") or "").upper()
    if not pair or direction not in {"LONG", "SHORT"}:
        return None
    hz = pay.get("horizon_s")
    try:
        hz_f = float(hz) if hz is not None else None
    except (TypeError, ValueError):
        hz_f = None
    # (t0_ms, pair, direction[, horizon_s[, seq]])
    if hz_f and seq is not None:
        return (int(t0), pair, direction, hz_f, int(seq))
    if hz_f:
        return (int(t0), pair, direction, hz_f)
    if seq is not None:
        return (int(t0), pair, direction, 0.0, int(seq))
    return (int(t0), pair, direction)


def load_subs_from_receipts(path: Path) -> list[tuple]:
    """Return diversity rows from local ingest receipts (preferred source)."""
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
        t0 = rec.get("grid_t0_ms")
        if t0 is None:
            t0 = sub.get("ts_miner")
        if t0 is None:
            continue
        seq = sub.get("seq")
        if seq is None:
            seq = rec.get("seq")
        try:
            seq_i = int(seq) if seq is not None else None
        except (TypeError, ValueError):
            seq_i = None
        row = _row_from_payload(pay, int(t0), seq=seq_i)
        if row:
            out.append(row)
    return out


def load_subs_from_submit_log(path: Path) -> list[tuple]:
    """`submit_ok` lines from hf_auto_loop JSONL (fallback / gap fill)."""
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
        t0_ms = int(float(ts) * 1000.0)
        hz = o.get("horizon_s")
        try:
            hz_f = float(hz) if hz is not None else None
        except (TypeError, ValueError):
            hz_f = None
        seq = o.get("seq")
        try:
            seq_i = int(seq) if seq is not None else None
        except (TypeError, ValueError):
            seq_i = None
        pay = {"trade_pair": pair, "direction": direction, "horizon_s": hz_f}
        row = _row_from_payload(pay, t0_ms, seq=seq_i)
        if row:
            out.append(row)
    return out


def _dedupe_key(row: tuple) -> tuple:
    """Prefer seq when present; else bucketed (pair, direction, t0).

    Bucket t0 to 2s so receipt grid_t0 vs local submit_ok clocks do not
    double-count the same fill when seq is missing on one side.
    """
    t0, pair, direction = row[0], row[1], row[2]
    seq = row[4] if len(row) > 4 else None
    if seq is not None and int(seq) > 0:
        return ("seq", int(seq))
    return ("t0", int(t0) // 2000, str(pair), str(direction))


def _near_dup(a: tuple, b: tuple, *, ms: int = 5_000) -> bool:
    """Same pair/dir within ``ms`` — catch seq-less receipt↔log duplicates."""
    return (
        str(a[1]).upper() == str(b[1]).upper()
        and str(a[2]).upper() == str(b[2]).upper()
        and abs(int(a[0]) - int(b[0])) <= int(ms)
    )


def load_diversity_subs(
    hotkey: str,
    *,
    log_path: Path | None = None,
    state_dir: Path | None = None,
) -> list[tuple]:
    """Prefer receipts; merge log gaps. De-dupe on seq / near-t0 (not pair alone)."""
    rec = load_subs_from_receipts(receipts_path(hotkey, state_dir))
    seen = {_dedupe_key(r) for r in rec}
    if log_path is not None:
        for r in load_subs_from_submit_log(log_path):
            key = _dedupe_key(r)
            if key in seen:
                continue
            # Seq-less log row that matches a receipt within 5s → same fill.
            if any(_near_dup(r, x) for x in rec):
                continue
            # Log seq that already appears on a receipt (clock-skew t0).
            seq = r[4] if len(r) > 4 else None
            if seq is not None and int(seq) > 0:
                if any(
                    len(x) > 4 and int(x[4]) == int(seq) for x in rec
                ):
                    continue
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


def is_minority_side(subs: list[tuple], pair: str, direction: str, now: float) -> bool:
    """True if direction is strictly under-represented on this pair in-window."""
    d = hf_mod.hf_diversity(subs, now)
    by = d.get("by_pair") or {}
    longs, shorts = by.get(str(pair).upper(), (0, 0))
    if isinstance(longs, (list, tuple)):
        longs, shorts = int(longs[0]), int(longs[1]) if len(longs) > 1 else 0
    direction = direction.upper()
    if direction == "LONG":
        return int(longs) < int(shorts)
    if direction == "SHORT":
        return int(shorts) < int(longs)
    return False


def diversity_guard_allows(
    subs: list[tuple],
    candidate: dict[str, Any],
    now: float,
    *,
    margin: float,
    guard_min_n: int,
    enabled: bool = True,
    mode: str | None = None,
) -> tuple[bool, dict, dict]:
    """Return (allow, current_div, projected_div).

    Modes (SN89_HF_DIVERSITY_GUARD_MODE):
      improve — allow if projected share ≥ floor+margin (already clear) OR
                projected share ≥ current share (repair / non-worsening).
                Prevents freeze when already above the floor: balanced or
                new-pair calls that keep share ≥ target must pass.
      target  — legacy: require share ≥ floor + margin in one step.
    """
    current = hf_mod.hf_diversity(subs, now)
    projected = project_diversity(subs, candidate, now)
    if not enabled:
        return True, current, projected
    n = int(projected.get("n") or 0)
    if n < int(guard_min_n):
        return True, current, projected

    mode = (mode or GUARD_MODE or "improve").strip().lower()
    cur_share = float(current.get("share") or 0.0)
    proj_share = float(projected.get("share") or 0.0)
    floor = float(projected.get("floor") or 0.0)
    target = floor + float(margin)

    if mode == "target":
        return proj_share + 1e-12 >= target, current, projected

    # improve (default): at/above target after the call, or non-worsening.
    if proj_share + 1e-12 >= target:
        return True, current, projected
    if proj_share + 1e-12 >= cur_share:
        return True, current, projected
    return False, current, projected


def minority_edge_ok(
    edge: float,
    *,
    min_edge: float,
    tol: float,
    is_minority: bool,
) -> bool:
    """Full min_edge for majority/normal; minority side may use -tol."""
    if edge >= float(min_edge):
        return True
    if is_minority and edge >= -float(tol):
        return True
    return False
