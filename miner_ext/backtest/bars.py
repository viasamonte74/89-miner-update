"""Build 1-minute mid bars for board crypto pairs from archived raw windows."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sn89_signals import config

from miner_ext.data.paths import DATA_ROOT, RAW_WINDOWS, ensure_dirs
from miner_ext.data.store import list_archived_windows

BARS_DIR = os.path.join(DATA_ROOT, "bars_1m")
SCHEMA = pa.schema(
    [
        ("t", pa.int64()),  # minute start ms
        ("o", pa.float64()),
        ("h", pa.float64()),
        ("l", pa.float64()),
        ("c", pa.float64()),
        ("n", pa.int32()),
    ]
)


def crypto_pairs() -> list[str]:
    return sorted(
        p
        for p, m in config.allowed_assets().items()
        if m.get("asset_class") == "crypto"
    )


def build_bars(pairs: list[str] | None = None, log_every: int = 500) -> dict:
    """One streaming pass over all windows → `{pair}.parquet` under bars_1m/."""
    ensure_dirs()
    os.makedirs(BARS_DIR, exist_ok=True)
    pairs = pairs or crypto_pairs()
    want = set(pairs)
    # pair -> minute_ms -> [o,h,l,c,n]
    state: dict[str, dict[int, list]] = {p: {} for p in pairs}
    windows = list_archived_windows()
    print(f"bars: {len(windows)} windows × {len(pairs)} pairs → {BARS_DIR}", flush=True)

    for i, w in enumerate(windows, 1):
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
                pair = d.get("a")
                if pair not in want:
                    continue
                t = int(d["t"])
                p = float(d["p"])
                m = (t // 60_000) * 60_000
                bucket = state[pair]
                if m not in bucket:
                    bucket[m] = [p, p, p, p, 1]
                else:
                    o, h, l, c, n = bucket[m]
                    bucket[m] = [o, max(h, p), min(l, p), p, n + 1]
        if i % log_every == 0:
            print(f"  scanned {i}/{len(windows)}", flush=True)

    out_counts = {}
    for pair in pairs:
        rows = []
        for m in sorted(state[pair]):
            o, h, l, c, n = state[pair][m]
            rows.append({"t": m, "o": o, "h": h, "l": l, "c": c, "n": n})
        path = os.path.join(BARS_DIR, f"{pair}.parquet")
        if not rows:
            out_counts[pair] = 0
            continue
        table = pa.Table.from_pylist(rows, schema=SCHEMA)
        tmp = path + ".tmp"
        pq.write_table(table, tmp, compression="zstd")
        os.replace(tmp, path)
        out_counts[pair] = len(rows)
        print(f"  {pair}: {len(rows)} minutes", flush=True)
    return out_counts


def load_bars(pair: str) -> np.ndarray:
    """Return structured array with fields t,o,h,l,c,n."""
    path = os.path.join(BARS_DIR, f"{pair}.parquet")
    table = pq.read_table(path)
    return table.to_pandas().to_records(index=False)


def load_close_series(pair: str) -> tuple[np.ndarray, np.ndarray]:
    """(t_ms sorted, close)."""
    rec = load_bars(pair)
    return rec["t"].astype(np.int64), rec["c"].astype(np.float64)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pair", action="append", default=None)
    args = p.parse_args(argv)
    build_bars(pairs=args.pair)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
