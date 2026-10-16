#!/usr/bin/env python3
"""Read-only daily fleet report from public /miner/<ss58> pages.

Does not submit, register, or touch wallets. Writes JSON under SN89_DATA_ROOT/reports/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from pathlib import Path

FLEET = {
    "rabi-h1": "5HEtPjhaCs859sdCjMG1a3TEZWFftKRs4VAYzCjnNUf82iDL",
    "rabi-h2": "5EXSBXEnKznf4hArQ3DmbZbyaaETBeeJ2MVKWvJqEDuRxbge",
    "rabi-h3": "5Gy1vJCTCdb4eitPynx5q5ZmFdU4ZMs65kSiBfE6j4ebnG2Z",
    "rabi-h4": "5CoXmAEZgxEqvqRMXWQLfV7HjuETtf9rWaAhDVGjwy67irdg",
}

DATA_ROOT = Path(os.environ.get("SN89_DATA_ROOT", "/workspace/sn89_data"))
UA = {"User-Agent": "sn89-fleet-report/1"}


def _fetch_miner(ss58: str) -> dict:
    url = f"https://infinitequant.app/miner/{ss58}"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    # Next.js flight payload embeds JSON; pull the largest JSON-looking blob
    # that mentions diversity / hit rate fields.
    blobs = re.findall(r"self\.__next_f\.push\(\[.*?\]\)", html, flags=re.S)
    best = {}
    for b in blobs:
        if "diversity" in b or "wilson" in b or "hit_rate" in b:
            # crude unescape of embedded strings
            for m in re.finditer(r"\{[^{}]{20,8000}\}", b):
                try:
                    o = json.loads(m.group(0))
                except json.JSONDecodeError:
                    continue
                if isinstance(o, dict) and len(o) > len(best):
                    best = o
    return {"ss58": ss58, "url": url, "parsed": best, "raw_len": len(html)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SN89 rabi fleet public board report")
    p.add_argument("--out-dir", default=str(DATA_ROOT / "reports"))
    args = p.parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, ss58 in FLEET.items():
        try:
            rows[name] = _fetch_miner(ss58)
            print(f"OK {name} bytes={rows[name]['raw_len']} keys={list((rows[name].get('parsed') or {}).keys())[:8]}")
        except Exception as e:  # noqa: BLE001
            rows[name] = {"ss58": ss58, "error": f"{type(e).__name__}: {e}"}
            print(f"FAIL {name}: {e}")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = out_dir / f"fleet_report_{stamp}.json"
    prev = sorted(out_dir.glob("fleet_report_*.json"))
    prev_path = prev[-1] if prev else None
    payload = {
        "ts": time.time(),
        "utc": stamp,
        "keys": rows,
        "previous": str(prev_path) if prev_path else None,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest = out_dir / "fleet_report_latest.json"
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
