#!/usr/bin/env python3
"""Scan fleet jsonl + on-chain incentive for rank-15 plan alerts.

Exit 1 if any alert fires. Focus: LF earners (h1/h4), dead HF quotes, no-call.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

LOG_DIR = Path(os.environ.get("SN89_LOG_DIR", "/workspace/logs"))
NO_CALL_S = float(os.environ.get("SN89_HF_NO_CALL_ALERT_S", str(6 * 3600)))
LF_NO_CALL_S = float(os.environ.get("SN89_LF_NO_CALL_ALERT_S", str(12 * 3600)))
LOOKBACK_S = float(os.environ.get("SN89_WATCHDOG_LOOKBACK_S", "900"))

# Rank-15 earners (stricter alerts). All four keys also run quality LF + HF.
LF_EARNERS = ("h1", "h4")
LF_ALL = ("h1", "h2", "h3", "h4")
HF_KEYS = ("h1", "h2", "h3", "h4")

FLEET = {
    "h1": "5HEtPjhaCs859sdCjMG1a3TEZWFftKRs4VAYzCjnNUf82iDL",
    "h2": "5EXSBXEnKznf4hArQ3DmbZbyaaETBeeJ2MVKWvJqEDuRxbge",
    "h3": "5Gy1vJCTCdb4eitPynx5q5ZmFdU4ZMs65kSiBfE6j4ebnG2Z",
    "h4": "5CoXmAEZgxEqvqRMXWQLfV7HjuETtf9rWaAhDVGjwy67irdg",
}


def _tail_events(path: Path, *, since: float) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-5000:]:
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = float(o.get("ts") or 0)
        if ts >= since:
            out.append(o)
    return out


def _last_submit_ok(path: Path) -> float | None:
    last = None
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") == "submit_ok" and o.get("ok", True):
            ts = float(o.get("ts") or 0)
            if ts > 0:
                last = ts if last is None else max(last, ts)
    return last


def _incentive_rank(ss58: str) -> tuple[float, int, int] | None:
    try:
        import bittensor as bt

        meta = bt.Subtensor(network="finney").metagraph(netuid=89)
        if ss58 not in meta.hotkeys:
            return None
        uid = meta.hotkeys.index(ss58)
        inc = float(meta.incentive[uid])
        ranks = sorted(
            ((float(meta.incentive[i]), i) for i in range(len(meta.incentive))),
            reverse=True,
        )
        rank = 1 + [r[1] for r in ranks].index(uid)
        return inc, rank, int(meta.block)
    except Exception:  # noqa: BLE001
        return None


def check_hf(hk: str, now: float) -> list[str]:
    alerts: list[str] = []
    hf = LOG_DIR / f"hf_auto_submit_{hk}.jsonl"
    since = now - LOOKBACK_S
    for ev in _tail_events(hf, since=since):
        if ev.get("event") == "watchdog_alert":
            alerts.append(f"{hk} HF watchdog_alert kind={ev.get('kind')}")
        if ev.get("event") == "live_quote_missing" and ev.get("all_dead"):
            alerts.append(
                f"{hk} HF live_quote_missing all_dead "
                f"missing={ev.get('missing')}/{ev.get('attempted')}"
            )
    last_hf = _last_submit_ok(hf)
    if last_hf is None or (now - last_hf) >= NO_CALL_S:
        silent = None if last_hf is None else round(now - last_hf, 1)
        feed_dead = any(
            ev.get("event") == "live_quote_missing" and ev.get("all_dead")
            for ev in _tail_events(hf, since=since)
        )
        tag = "no_call_live_quote_dead" if feed_dead else "hf_no_call"
        alerts.append(f"{hk} {tag} silent_s={silent}")
    return alerts


def check_lf_earner(hk: str, now: float) -> list[str]:
    alerts: list[str] = []
    lf = LOG_DIR / f"lf_auto_submit_{hk}.jsonl"
    stop = Path(
        os.environ.get("SN89_DATA_ROOT", "/workspace/sn89_data")
    ) / f"STOP_AUTO_SUBMIT_{hk.upper()}"
    if stop.exists():
        alerts.append(f"{hk} LF STOP file present ({stop.name}) — earner parked?")
        return alerts
    last = _last_submit_ok(lf)
    # Soft: long silence is OK on quality-only days; hard alert at 12h with no loop.
    recent = _tail_events(lf, since=now - 3600)
    if not recent and lf.exists():
        # process may be dead
        mtime = lf.stat().st_mtime
        if now - mtime > 3600:
            alerts.append(f"{hk} LF log idle >1h (process down?)")
    if last is not None and (now - last) >= LF_NO_CALL_S:
        alerts.append(f"{hk} LF no_submit silent_s={round(now - last, 1)}")
    # Today cap
    day = int(now // 86400)
    n_today = 0
    if lf.exists():
        for line in lf.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("event") == "submit_ok" and o.get("ok", True):
                if int(float(o.get("ts") or 0) // 86400) == day:
                    n_today += 1
    if n_today > 1:
        alerts.append(f"{hk} LF over_cap today={n_today} (plan max 1)")
    return alerts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SN89 fleet rank-15 watchdog")
    p.add_argument("--skip-meta", action="store_true")
    args = p.parse_args(argv)
    now = time.time()
    all_alerts: list[str] = []

    for hk in LF_ALL:
        all_alerts.extend(check_lf_earner(hk, now))
    for hk in HF_KEYS:
        all_alerts.extend(check_hf(hk, now))

    if not args.skip_meta:
        for hk in LF_EARNERS:
            ss58 = FLEET.get(hk)
            if not ss58:
                continue
            info = _incentive_rank(ss58)
            if info is None:
                continue
            inc, rank, block = info
            print(f"INFO {hk} incentive={inc:.5f} rank={rank} block={block}")
            if rank > 40:
                all_alerts.append(f"{hk} rank_slip rank={rank} incentive={inc:.5f}")

    if not all_alerts:
        print("OK — no watchdog alerts")
        return 0
    for a in all_alerts:
        print(f"ALERT {a}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
