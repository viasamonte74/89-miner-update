"""Cheap health snapshot: disk, data freshness, archiver state."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from miner_ext.data.paths import ARCHIVE_STATE, DATA_ROOT, RAW_WINDOWS
from miner_ext.data.store import list_archived_windows


def main() -> int:
    windows = list_archived_windows()
    usage = shutil.disk_usage(DATA_ROOT if os.path.isdir(DATA_ROOT) else "/workspace")
    state = {}
    try:
        with open(ARCHIVE_STATE, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        pass
    last_w = windows[-1] if windows else None
    age_h = None
    if last_w is not None:
        age_h = (time.time() * 1000 - last_w) / 3_600_000
    out = {
        "n_windows": len(windows),
        "last_window_ms": last_w,
        "last_window_age_h": age_h,
        "disk_free_gb": round(usage.free / 1e9, 2),
        "disk_used_gb": round(usage.used / 1e9, 2),
        "archiver_state": {
            k: state.get(k) for k in ("ok", "skip", "fail", "last_w", "finished_pass_at")
        },
        "ts": time.time(),
    }
    print(json.dumps(out, indent=2))
    # soft warn if data > 6h stale once we have history
    if age_h is not None and age_h > 6:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
