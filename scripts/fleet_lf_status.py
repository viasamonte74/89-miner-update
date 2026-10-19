#!/usr/bin/env python3
"""One-shot LF earner status for the rank-15 plan (h1 / h4)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LOG_DIR = Path(os.environ.get("SN89_LOG_DIR", "/workspace/logs"))
KEYS = ("h1", "h2", "h3", "h4")
ROLE = {
    "h1": "rank15",
    "h2": "quality",
    "h3": "quality",
    "h4": "rank15",
}


def main() -> int:
    now = time.time()
    day = int(now // 86400)
    print(f"utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}")
    print("plan: h1/h4 rank-15 (1 HQ/day); h2/h3 quality rebuild (1/day); all run HF")
    for hk in KEYS:
        p = LOG_DIR / f"lf_auto_submit_{hk}.jsonl"
        if not p.exists():
            print(f"{hk}: no log")
            continue
        today = 0
        last = None
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("event") == "submit_ok" and o.get("ok", True):
                ts = float(o.get("ts") or 0)
                last = (o.get("utc"), o.get("pair"), o.get("direction"), ts)
                if int(ts // 86400) == day:
                    today += 1
        start = None
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("event") == "start":
                start = o
                break
        print(
            f"{hk}[{ROLE[hk]}]: today={today}/1 last={last[0] if last else None} "
            f"{(last[1] + ':' + last[2]) if last else '-'} "
            f"max_day={start.get('lf_max_per_day') if start else '?'} "
            f"early_score={start.get('early_score') if start else '?'} "
            f"vol_early={start.get('min_vol_band_early') if start else '?'} "
            f"wash_hard={start.get('lf_wash_hard') if start else '?'} "
            f"net_pair={start.get('network_live_block_pair') if start else '?'}"
        )
    print("note: board W/L/LB — h1/h4 next win at 9W/5L qualifies; h2/h3 need a long win streak")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
