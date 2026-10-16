"""Paper-mode runner stub (Phase 4). Logs would-be calls; never commits on-chain."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

LOG = Path("/workspace/logs/paper_decisions.jsonl")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "mode": "paper",
        "action": "skip",
        "reason": "signal engine not wired yet (Phase 3+)",
    }
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
