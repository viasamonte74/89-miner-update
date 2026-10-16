"""Batch-build per-pair parquet from archived raw windows.

Raw windows remain the grader source of truth. Parquet is for feature/training
pipelines (Phase 2+). Rebuild is idempotent: overwrites `{pair}.parquet`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq

# repo root on path when run as module
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config  # noqa: E402

from .paths import PARQUET_DIR, RAW_WINDOWS, ensure_dirs
from .store import list_archived_windows

SCHEMA = pa.schema(
    [
        ("t", pa.int64()),
        ("p", pa.float64()),
        ("b", pa.float64()),
        ("k", pa.float64()),
        ("s", pa.string()),
        ("w", pa.int64()),
    ]
)


def _board_pairs(crypto_only: bool = True) -> list[str]:
    bands = config.allowed_assets()
    pairs = []
    for pair, meta in bands.items():
        cls = meta.get("asset_class", "")
        if crypto_only and cls != "crypto":
            continue
        pairs.append(pair)
    return sorted(pairs)


def rebuild_pair(pair: str, windows: list[int] | None = None) -> int:
    """Stream all windows for one pair → single sorted deduped parquet."""
    ensure_dirs()
    windows = windows if windows is not None else list_archived_windows()
    rows: list[dict] = []
    seen_t: set[int] = set()
    for w in windows:
        path = os.path.join(RAW_WINDOWS, str(w), "ticks.jsonl")
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("a") != pair:
                    continue
                t = int(d["t"])
                if t in seen_t:
                    continue
                seen_t.add(t)
                rows.append(
                    {
                        "t": t,
                        "p": float(d["p"]),
                        "b": float(d.get("b") or d["p"]),
                        "k": float(d.get("k") or d["p"]),
                        "s": str(d.get("s") or ""),
                        "w": w,
                    }
                )
    rows.sort(key=lambda r: r["t"])
    path = os.path.join(PARQUET_DIR, f"{pair}.parquet")
    if not rows:
        if os.path.exists(path):
            os.remove(path)
        return 0
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    tmp = path + ".tmp"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)
    return len(rows)


def convert_new_windows(windows_filter=None, crypto_only: bool = True) -> dict:
    """Rebuild parquet for board pairs from whatever is on disk.

    `windows_filter` kept for API compat with the archiver; rebuild uses all
    archived windows so dedupe stays correct.
    """
    del windows_filter  # full rebuild is safer than incremental append
    pairs = _board_pairs(crypto_only=crypto_only)
    windows = list_archived_windows()
    print(
        f"ingest rebuild: {len(pairs)} pairs × {len(windows)} windows",
        flush=True,
    )
    total = 0
    for i, pair in enumerate(pairs, 1):
        n = rebuild_pair(pair, windows)
        total += n
        print(f"  [{i}/{len(pairs)}] {pair}: {n} ticks", flush=True)
    return {"pairs": len(pairs), "windows": len(windows), "rows": total}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--all-classes", action="store_true")
    p.add_argument("--pair", default=None)
    args = p.parse_args(argv)
    if args.pair:
        n = rebuild_pair(args.pair)
        print(f"{args.pair}: {n} ticks")
        return 0
    convert_new_windows(crypto_only=not args.all_classes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
