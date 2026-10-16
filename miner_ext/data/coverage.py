"""Coverage report over archived raw windows."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config

from .paths import COVERAGE_PATH, ensure_dirs
from .store import list_archived_windows, WINDOW_MS
from .paths import RAW_WINDOWS


def _board_crypto() -> set[str]:
    return {
        p
        for p, m in config.allowed_assets().items()
        if m.get("asset_class") == "crypto"
    }


def report(sample_every: int = 50) -> dict:
    """Scan archived windows; sample pair presence (full scan is expensive)."""
    ensure_dirs()
    windows = list_archived_windows()
    crypto = _board_crypto()
    if not windows:
        out = {"n_windows": 0, "note": "no archived windows"}
        with open(COVERAGE_PATH, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        return out

    gaps = []
    for a, b in zip(windows, windows[1:]):
        if b - a > WINDOW_MS:
            gaps.append({"from": a, "to": b, "missing_ms": b - a - WINDOW_MS})

    # sample pair counts from evenly spaced windows
    sample_idx = list(range(0, len(windows), max(1, sample_every)))
    if sample_idx[-1] != len(windows) - 1:
        sample_idx.append(len(windows) - 1)
    pair_hits: Counter = Counter()
    for i in sample_idx:
        w = windows[i]
        path = os.path.join(RAW_WINDOWS, str(w), "ticks.jsonl")
        seen = set()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                a = json.loads(line).get("a")
                if a in crypto:
                    seen.add(a)
        for a in seen:
            pair_hits[a] += 1

    out = {
        "n_windows": len(windows),
        "first_w": windows[0],
        "last_w": windows[-1],
        "span_days": (windows[-1] - windows[0]) / WINDOW_MS * 180 / 86400,
        "gap_count": len(gaps),
        "gaps_head": gaps[:20],
        "sample_windows": len(sample_idx),
        "crypto_pair_presence_in_sample": {
            p: pair_hits.get(p, 0) for p in sorted(crypto)
        },
    }
    with open(COVERAGE_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps({k: out[k] for k in out if k != "gaps_head"}, indent=2))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample-every", type=int, default=50)
    args = p.parse_args(argv)
    report(sample_every=args.sample_every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
