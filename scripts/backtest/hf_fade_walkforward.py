#!/usr/bin/env python3
"""Walk-forward 1m-bar backtest for HF direction rules (fade vs trend).

Writes miner_ext/results/HF_FADE_WALKFORWARD.md + .json.
This is a bar-level approximation (not tick 2-touch grader parity).
Require a fresh report before changing SN89_HF_DIR_MODE / volume knobs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

from miner_ext.backtest.baselines import momentum_score, realized_stats  # noqa: E402
from miner_ext.data.paths import DATA_ROOT  # noqa: E402
from sn89_signals import hf as hf_mod  # noqa: E402

import hf_fade_spike  # noqa: E402

BARS_DIR = os.path.join(DATA_ROOT, "bars_1m")
OUT_DIR = _REPO / "miner_ext" / "results"


def _load_close(pair: str) -> tuple[np.ndarray, np.ndarray] | None:
    import pyarrow.parquet as pq

    path = os.path.join(BARS_DIR, f"{pair}.parquet")
    if not os.path.isfile(path):
        return None
    tbl = pq.read_table(path, columns=["t", "c"])
    return (
        tbl.column("t").to_numpy().astype(np.int64),
        tbl.column("c").to_numpy().astype(np.float64),
    )


def _grade_bar(
    t_ms: np.ndarray,
    close: np.ndarray,
    i0: int,
    direction: str,
    tp_bps: float,
    horizon_min: int,
) -> str | None:
    """Simple 1m first-touch grader. Both sides same bar → loss."""
    if i0 + horizon_min >= len(close):
        return None
    entry = float(close[i0])
    if entry <= 0:
        return None
    up = entry * (1.0 + tp_bps / 10_000.0)
    dn = entry * (1.0 - tp_bps / 10_000.0)
    for j in range(i0 + 1, i0 + horizon_min + 1):
        c = float(close[j])
        hit_up = c >= up
        hit_dn = c <= dn
        if hit_up and hit_dn:
            return "lost"
        if direction == "LONG":
            if hit_up:
                return "won"
            if hit_dn:
                return "lost"
        else:
            if hit_dn:
                return "won"
            if hit_up:
                return "lost"
    return "open"


def _wilson_lb(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    phat = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = phat + z2 / (2.0 * n)
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z2 / (4.0 * n * n))
    return max(0.0, (centre - margin) / denom)


def summarize(rows: list[dict]) -> dict:
    dec = [r for r in rows if r["result"] in ("won", "lost")]
    w = sum(1 for r in dec if r["result"] == "won")
    n = len(dec)
    hr = w / n if n else None
    return {
        "n_decided": n,
        "wins": w,
        "losses": n - w,
        "hit_rate": hr,
        "wilson_lb": _wilson_lb(w, n) if n else None,
    }


def run(
    *,
    pairs: list[str],
    window_min: int,
    min_z: float,
    step_min: int,
    horizon_min: int,
) -> dict:
    now = time.time()
    board = hf_mod.hf_bands_as_of(now) or {}
    all_rows: dict[str, list[dict]] = defaultdict(list)

    for pair in pairs:
        series = _load_close(pair)
        if series is None:
            continue
        t_ms, close = series
        row = board.get(pair)
        if not row:
            continue
        tp_bps = float(row[0])
        hz_s = int(row[2])
        if hz_s > 1800:
            continue
        # Sample every step_min bars, leave lookback + horizon margin
        start = max(120, window_min + 5)
        end = len(close) - horizon_min - 1
        for i in range(start, end, step_min):
            decision_ms = int(t_ms[i])
            # fade
            fade = hf_fade_spike.fade_direction(
                t_ms,
                close,
                decision_ms,
                window_min=window_min,
                min_abs_z=min_z,
            )
            if fade is not None:
                res = _grade_bar(
                    t_ms, close, i, fade["direction"], tp_bps, horizon_min
                )
                if res in ("won", "lost"):
                    all_rows["fade_spike"].append(
                        {
                            "pair": pair,
                            "direction": fade["direction"],
                            "result": res,
                            "t": decision_ms / 1000.0,
                            "z": fade["abs_z"],
                        }
                    )
            # trend: follow last 2h momentum
            mom = momentum_score(t_ms, close, decision_ms, lag_min=120)
            if mom is not None and abs(mom) > 1e-8:
                d = "LONG" if mom > 0 else "SHORT"
                res = _grade_bar(t_ms, close, i, d, tp_bps, horizon_min)
                if res in ("won", "lost"):
                    all_rows["trend_2h"].append(
                        {
                            "pair": pair,
                            "direction": d,
                            "result": res,
                            "t": decision_ms / 1000.0,
                        }
                    )
            # always long
            res = _grade_bar(t_ms, close, i, "LONG", tp_bps, horizon_min)
            if res in ("won", "lost"):
                all_rows["always_long"].append(
                    {"pair": pair, "direction": "LONG", "result": res, "t": decision_ms / 1000.0}
                )

    report = {"generated_at": now, "pairs": pairs, "rules": {}}
    lines = [
        "# HF fade walk-forward (1m bars)",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}",
        f"Pairs: {', '.join(pairs)}",
        f"Fade window={window_min}m min_z={min_z}; horizon≈{horizon_min}m; step={step_min}m",
        "",
        "Caveat: bar first-touch grader (both-touch→loss). Not tick 2-touch parity.",
        "",
        "| rule | n | HR | Wilson LB | 1st half HR | 2nd half HR |",
        "|------|--:|---:|----------:|------------:|------------:|",
    ]
    for rule, rows in all_rows.items():
        if not rows:
            continue
        rows = sorted(rows, key=lambda r: r["t"])
        mid = len(rows) // 2
        full = summarize(rows)
        h1 = summarize(rows[:mid])
        h2 = summarize(rows[mid:])
        report["rules"][rule] = {"full": full, "half1": h1, "half2": h2}
        lines.append(
            f"| {rule} | {full['n_decided']} | "
            f"{(full['hit_rate'] or 0):.3f} | {(full['wilson_lb'] or 0):.3f} | "
            f"{(h1['hit_rate'] or 0):.3f} | {(h2['hit_rate'] or 0):.3f} |"
        )
    lines.extend(
        [
            "",
            "## Gate for live changes",
            "",
            "- Prefer fade_spike when both halves ≥ always_long and HR ≥ 0.51 with n≥1000.",
            "- Do not raise SN89_HF_MAX_PER_DAY on a rule whose halves disagree by >3pp.",
            "- Tick grader-parity backtest still required before trusting production HR.",
            "",
        ]
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUT_DIR / "HF_FADE_WALKFORWARD.md"
    js_path = OUT_DIR / "HF_FADE_WALKFORWARD.json"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"))
    print(f"wrote {md_path} and {js_path}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--pairs",
        default="SUIUSD,ALGOUSD,ADAUSD,KPEPEUSD,NEARUSD,ZROUSD,TAOUSD,ARBUSD",
    )
    ap.add_argument("--window-min", type=int, default=5)
    ap.add_argument("--min-z", type=float, default=2.0)
    ap.add_argument("--step-min", type=int, default=15)
    ap.add_argument("--horizon-min", type=int, default=30)
    args = ap.parse_args()
    pairs = [p.strip().upper() for p in args.pairs.split(",") if p.strip()]
    run(
        pairs=pairs,
        window_min=args.window_min,
        min_z=args.min_z,
        step_min=args.step_min,
        horizon_min=args.horizon_min,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
