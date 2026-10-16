#!/usr/bin/env python3
"""HF auto-submitter — runs concurrent with LF on the same hotkey.

Rules respected
---------------
  • HF board bands/horizons (cannot choose)
  • ≤30/UTC day, polite min gap (default 10 min; protocol allows 250 ms)
  • Cross-mechanism pair lock: skip pairs this hotkey still has live on LF
  • One open HF position per pair (claim until horizon)
  • Multi-hotkey anti-copy: pick_rank + shared HF fleet claims

Scoring (decisive-first, same spirit as LF):
  short realized vol / HF band over ~horizon; require recent expansion.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import bittensor as bt

from miner_ext.backtest.baselines import (
    first_passage_p_long,
    momentum_score,
    realized_stats,
)
from miner_ext.data.paths import DATA_ROOT
from neurons.miner import submit_hf
from sn89_signals import hf as hf_mod

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import fleet_priors  # noqa: E402

LOG = Path(os.environ.get("SN89_HF_AUTO_LOG", "/workspace/logs/hf_auto_submit.jsonl"))
STOP = Path(os.environ.get("SN89_HF_AUTO_STOP", "/workspace/sn89_data/STOP_HF_AUTO"))
STATE = Path(
    os.environ.get(
        "SN89_HF_AUTO_STATE", os.path.expanduser("~/.sn89/hf_auto_state.json")
    )
)
CLAIM_DIR = Path(
    os.environ.get("SN89_HF_CLAIM_DIR", "/workspace/sn89_data/hf_fleet_claims")
)
BARS_DIR = os.path.join(DATA_ROOT, "bars_1m")
# LF logs — to honor cross-mechanism pair lock for THIS hotkey
LF_LOG = Path(os.environ.get("SN89_AUTO_LOG", "/workspace/logs/lf_auto_submit.jsonl"))
LF_CLAIM_DIR = Path(
    os.environ.get("SN89_AUTO_CLAIM_DIR", "/workspace/sn89_data/fleet_claims")
)

# Eligibility needs ~50 / 8 trading days ≈ 6.3/day. Cap below protocol 30 and
# space submits so we only fire when a high-quality setup is open.
MAX_PER_DAY = int(os.environ.get("SN89_HF_MAX_PER_DAY", "8"))
# Cap concentration on one pair per UTC day (SUI dominated prior book).
MAX_PER_PAIR_PER_DAY = int(os.environ.get("SN89_HF_MAX_PER_PAIR_PER_DAY", "2"))
MIN_GAP_S = float(os.environ.get("SN89_HF_MIN_GAP_S", "3600"))  # 1h between fires
POLL_S = float(os.environ.get("SN89_HF_POLL_S", "45"))
MIN_VOL_BAND = float(os.environ.get("SN89_HF_MIN_VOL_BAND", "1.30"))
MIN_VOL_BAND_SHORT = float(os.environ.get("SN89_HF_MIN_VOL_BAND_SHORT", "0.75"))
MIN_SCORE = float(os.environ.get("SN89_HF_MIN_SCORE", "1.00"))
MIN_EDGE = float(os.environ.get("SN89_HF_MIN_EDGE", "0.03"))
# Must stay within this fraction of the *board* #1 score (not fleet leftovers).
MIN_TOP_RATIO = float(os.environ.get("SN89_HF_MIN_TOP_RATIO", "0.85"))
# Lower ranks may use a softer top-ratio so they can fill when board is thin.
MIN_TOP_RATIO_OVERFLOW = float(
    os.environ.get("SN89_HF_MIN_TOP_RATIO_OVERFLOW", "0.75")
)
# Free-best: after sibling/claim filters, take best free setup (don't hard-slice
# by pick_rank — that starved h3/h4 when n_quality≈1). pick_rank only staggers.
FREE_BEST = os.environ.get("SN89_HF_FREE_BEST", "1").strip() not in (
    "0",
    "false",
    "False",
)
# Strict qualify mode: proven pairs + higher score floor.
STRICT_MODE = os.environ.get("SN89_STRICT_MODE", "0").strip() not in (
    "0",
    "false",
    "False",
)
# Default ON: allow both dirs on each strict pair. One-dir-only books fail the
# per-pair two-sidedness gate (hf.hf_diversity) once n≥HF_DIVERSITY_MIN_SUBS.
STRICT_BOTH_DIRS = os.environ.get("SN89_STRICT_BOTH_DIRS", "1").strip() not in (
    "0",
    "false",
    "False",
)
_STRICT_PD_RAW = os.environ.get(
    "SN89_STRICT_PAIR_DIRS",
    "ADAUSD:SHORT,SUIUSD:SHORT,ALGOUSD:SHORT,XRPUSD:SHORT,NEARUSD:SHORT,"
    "KPEPEUSD:LONG,WLDUSD:LONG,BCHUSD:LONG,LINKUSD:LONG",
)
_STRICT_PD_BASE = frozenset(
    tuple(x.strip().upper().split(":", 1))
    for x in _STRICT_PD_RAW.split(",")
    if ":" in x.strip()
)
if STRICT_BOTH_DIRS:
    STRICT_PAIR_DIRS = frozenset(
        (p, d)
        for p, _ in _STRICT_PD_BASE
        for d in ("LONG", "SHORT")
    ) | _STRICT_PD_BASE
else:
    STRICT_PAIR_DIRS = _STRICT_PD_BASE
STRICT_MIN_SCORE = float(os.environ.get("SN89_STRICT_MIN_SCORE", "1.25"))
# Validator pair lock (24h) — must not treat horizon (often 30m–8h) as unlock.
PAIR_LOCK_S = float(os.environ.get("SN89_PAIR_LOCK_S", str(hf_mod.PAIR_LOCK_S)))
# Prefer faster 30m horizons when scoring equal
PREFER_HZ_S = int(os.environ.get("SN89_HF_PREFER_HZ_S", "1800"))
BAR_REFRESH_S = float(os.environ.get("SN89_HF_BAR_REFRESH_S", "300"))
BAR_MAX_AGE_MIN = float(os.environ.get("SN89_HF_BAR_MAX_AGE_MIN", "5"))

# Crypto HF pairs we have 1m bars for (skip equities/FX without archive).
# Ordered toward low-wash / top-miner LF cores (still scored; HF board gates apply).
DEFAULT_PAIRS = [
    "ZROUSD",
    "TAOUSD",
    "ALGOUSD",
    "NEARUSD",
    "SUIUSD",
    "LTCUSD",
    "BCHUSD",
    "UNIUSD",
    "AVAXUSD",
    "WLDUSD",
    "ENAUSD",
    "ADAUSD",
    "LINKUSD",
    "DOTUSD",
    "KPEPEUSD",
    "XRPUSD",
    "BNBUSD",
    "SOLUSD",
    "HYPEUSD",
    "DOGEUSD",
    "ARBUSD",
    "CRVUSD",
    "ETHUSD",
    "BTCUSD",
]


def _log(rec: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **rec,
    }
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def _day_id(now: float) -> int:
    return int(now // 86_400)


def _sleep(sec: float, reason: str) -> None:
    sec = max(1.0, float(sec))
    _log({"event": "sleep", "seconds": round(sec, 1), "reason": reason})
    end = time.time() + sec
    while time.time() < end:
        if STOP.exists():
            _log({"event": "stop_file", "path": str(STOP)})
            raise SystemExit(0)
        time.sleep(min(30.0, end - time.time()))


def _sibling_hf_logs() -> list[Path]:
    raw = os.environ.get("SN89_HF_SIBLING_LOGS", "").strip()
    out: list[Path] = []
    for part in raw.split(":"):
        part = part.strip()
        if not part:
            continue
        p = Path(part)
        try:
            if p.resolve() == LOG.resolve():
                continue
        except OSError:
            pass
        out.append(p)
    return out


def _live_pairs_from_log(
    path: Path,
    now: float,
    *,
    by_pair_only: bool,
    live_s: float | None = None,
) -> set:
    """Pairs (or pair,dir) still within lock window from submit_ok lines.

    ``live_s`` overrides horizon. When omitted, uses max(horizon, PAIR_LOCK_S)
    so an 8h default horizon cannot unlock a pair before the 24h validator lock.
    """
    live: set = set()
    if not path.exists():
        return live
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return live
    for line in text.splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Count confirmed ok and ambiguous attempts (commit may have landed).
        ev = o.get("event")
        if ev not in {"submit_ok", "submit_ambiguous"}:
            continue
        if ev == "submit_ok" and not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        if live_s is not None:
            hold = float(live_s)
        else:
            hz = float(o.get("horizon_s") or 8 * 3600)
            hold = max(hz, PAIR_LOCK_S)
        if now - ts > hold:
            continue
        pair = (o.get("pair") or "").upper()
        direction = (o.get("direction") or "").upper()
        if not pair:
            continue
        if by_pair_only:
            live.add(pair)
        elif direction:
            live.add((pair, direction))
    return live


def _lf_locked_pairs_for_hotkey(now: float) -> set[str]:
    """Pairs this hotkey must not HF (24h cross-mechanism lock + open LF).

    Only this process's LF submit log counts — sibling LF positions do not lock us.
    """
    locked = _live_pairs_from_log(LF_LOG, now, by_pair_only=True, live_s=PAIR_LOCK_S)
    locked |= _live_pairs_from_log(LF_LOG, now, by_pair_only=True)
    if LF_CLAIM_DIR.exists():
        try:
            our_lf = str(LF_LOG.resolve())
        except OSError:
            our_lf = str(LF_LOG)
        for path in LF_CLAIM_DIR.glob("*.json"):
            try:
                o = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if float(o.get("until") or 0) <= now:
                continue
            log_s = str(o.get("log") or "")
            if log_s and (log_s == our_lf or log_s == str(LF_LOG)):
                locked.add(str(o.get("pair") or "").upper())
    return {p for p in locked if p}


def _own_locked_pairs(now: float) -> set[str]:
    """Own HF pairs still under validator pair lock or open horizon."""
    locked = _live_pairs_from_log(LOG, now, by_pair_only=True, live_s=PAIR_LOCK_S)
    locked |= _live_pairs_from_log(LOG, now, by_pair_only=True)
    return locked


def _dir_counts_from_log(path: Path, now: float, window_s: float) -> dict[str, list[int]]:
    """pair -> [n_long, n_short] in trailing window (for two-sided preference)."""
    by_pair: dict[str, list[int]] = {}
    if not path.exists():
        return by_pair
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return by_pair
    for line in text.splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        if now - ts > window_s:
            continue
        pair = (o.get("pair") or "").upper()
        direction = (o.get("direction") or "").upper()
        if not pair or direction not in {"LONG", "SHORT"}:
            continue
        lp = by_pair.setdefault(pair, [0, 0])
        lp[0 if direction == "LONG" else 1] += 1
    return by_pair


def _prefer_minority_side(ranked: list[dict], now: float) -> list[dict]:
    """Stable-sort so under-represented side per pair comes first (diversity gate)."""
    window = float(getattr(hf_mod, "HF_DIVERSITY_WINDOW_S", 30 * 86400))
    counts = _dir_counts_from_log(LOG, now, window)
    if not counts:
        return ranked

    def key(r: dict) -> tuple:
        pair = r["pair"]
        d = r["direction"]
        longs, shorts = counts.get(pair, [0, 0])
        # Prefer the side that increases min(L,S) for this pair.
        if d == "LONG":
            minority_gain = 1 if longs < shorts else (0 if longs == shorts else -1)
        else:
            minority_gain = 1 if shorts < longs else (0 if longs == shorts else -1)
        return (-minority_gain, -float(r.get("score") or 0))

    return sorted(ranked, key=key)


def _hf_sibling_live(now: float) -> set[tuple[str, str]]:
    live: set[tuple[str, str]] = set()
    for path in _sibling_hf_logs():
        live |= _live_pairs_from_log(path, now, by_pair_only=False)  # type: ignore
    if CLAIM_DIR.exists():
        for path in CLAIM_DIR.glob("*.json"):
            try:
                o = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if float(o.get("until") or 0) <= now:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            pair, direction = o.get("pair"), o.get("direction")
            if pair and direction:
                live.add((str(pair).upper(), str(direction).upper()))
    return live


def _try_claim(pair: str, direction: str, *, hotkey: str, now: float, horizon_s: float) -> bool:
    CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{pair.upper()}_{direction.upper()}"
    path = CLAIM_DIR / f"{key}.json"
    lock_path = CLAIM_DIR / f"{key}.lock"
    payload = {
        "pair": pair.upper(),
        "direction": direction.upper(),
        "hotkey": hotkey,
        "ts": now,
        "until": now + float(horizon_s),
        "log": str(LOG),
    }
    lock_path.touch(exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    existing = {}
                until = float(existing.get("until") or 0)
                owner = existing.get("hotkey")
                if until > now and owner and owner != hotkey:
                    return False
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return True
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _bars_age_min() -> float | None:
    path = os.path.join(BARS_DIR, "BTCUSD.parquet")
    if not os.path.isfile(path):
        return None
    try:
        t = pq.read_table(path, columns=["t"]).column("t").to_numpy()
        if len(t) == 0:
            return None
        return (time.time() * 1000.0 - float(t[-1])) / 60_000.0
    except Exception:  # noqa: BLE001
        return None


def _refresh_bars(pairs: list[str]) -> None:
    """Rebuild 1m bars; flock so only one HF/LF process rebuilds at a time."""
    from miner_ext.backtest.bars import build_bars

    lock_path = Path("/workspace/sn89_data/bars_refresh.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            age = _bars_age_min()
            if age is not None and age < BAR_MAX_AGE_MIN:
                _log({"event": "bars_fresh_skip", "age_min": round(age, 1)})
                return
            t0 = time.time()
            _log({"event": "bars_refresh_start", "n_pairs": len(pairs), "age_min": age})
            build_bars(pairs=pairs)
            _log(
                {
                    "event": "bars_refresh_done",
                    "wall_s": round(time.time() - t0, 1),
                    "age_min": round(_bars_age_min() or -1, 1),
                }
            )
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _load_close(pair: str) -> tuple[np.ndarray, np.ndarray] | None:
    path = os.path.join(BARS_DIR, f"{pair}.parquet")
    if not os.path.isfile(path):
        return None
    try:
        tbl = pq.read_table(path, columns=["t", "c"])
        t = tbl.column("t").to_numpy().astype(np.int64)
        c = tbl.column("c").to_numpy().astype(np.float64)
        return t, c
    except Exception:  # noqa: BLE001
        return None


def _passes_quality(
    r: dict,
    *,
    board_best: float,
    now: float | None = None,
    pick_rank: int = 0,
) -> bool:
    if fleet_priors.hf_pair_dir_blocked(r["pair"], r["direction"]):
        return False
    if STRICT_MODE:
        if (r["pair"], r["direction"]) not in STRICT_PAIR_DIRS:
            return False
        if r["score"] < STRICT_MIN_SCORE:
            return False
    elif r["score"] < MIN_SCORE:
        return False
    if r["vol_band"] < MIN_VOL_BAND:
        return False
    if r["vol_band_short"] < MIN_VOL_BAND_SHORT:
        return False
    if r["edge"] < MIN_EDGE:
        return False
    top_ratio = MIN_TOP_RATIO if pick_rank <= 0 else MIN_TOP_RATIO_OVERFLOW
    if board_best > 0 and r["score"] < top_ratio * board_best:
        return False
    floor = STRICT_MIN_SCORE if STRICT_MODE else MIN_SCORE
    if now is not None and fleet_priors.hf_hour_blocked(
        now, score=float(r["score"]), min_score=floor
    ):
        return False
    return True


def _submits_today(now: float) -> list[float]:
    if not LOG.exists():
        return []
    day = _day_id(now)
    out = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = o.get("event")
        if ev == "submit_ok" and o.get("ok", True):
            pass
        elif ev == "submit_ambiguous":
            pass
        else:
            continue
        ts = float(o.get("ts") or 0)
        if _day_id(ts) == day:
            out.append(ts)
    return out


def _pair_counts_today(now: float) -> dict[str, int]:
    """Successful HF submits per pair today (this hotkey's log)."""
    counts: dict[str, int] = {}
    if not LOG.exists():
        return counts
    day = _day_id(now)
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        if _day_id(ts) != day:
            continue
        pair = str(o.get("pair") or "").upper()
        if pair:
            counts[pair] = counts.get(pair, 0) + 1
    return counts


def score_hf(
    pair: str,
    direction: str,
    now: float,
    tp_bps: float,
    horizon_s: int,
    cache: dict,
) -> dict | None:
    if fleet_priors.hf_pair_dir_blocked(pair, direction):
        return None
    if pair not in cache:
        series = _load_close(pair)
        if series is None:
            return None
        cache[pair] = series
    t_ms, close = cache[pair]
    decision_ms = int(now * 1000)
    lookback = max(60, min(720, int(horizon_s // 60) * 4))
    stats = realized_stats(t_ms, close, decision_ms, lookback_min=lookback)
    stats_short = realized_stats(
        t_ms, close, decision_ms, lookback_min=max(30, int(horizon_s // 60))
    )
    mom = momentum_score(t_ms, close, decision_ms, lag_min=min(30, lookback // 2))
    if stats is None or stats_short is None or mom is None:
        return None
    _, mu, sig = stats
    _, _, sig_s = stats_short
    if sig <= 0 or sig_s <= 0 or tp_bps <= 0:
        return None
    band_frac = tp_bps / 10_000.0
    n_min = max(1.0, horizon_s / 60.0)
    vol_band = float(sig) * (n_min ** 0.5) / band_frac
    # recent ~1/4 horizon
    n_short = max(1.0, n_min / 4.0)
    vol_band_short = float(sig_s) * (n_short ** 0.5) / band_frac
    fp = first_passage_p_long(mu, sig, tp_bps)
    if direction == "LONG":
        edge = float(fp - 0.5)
        mom_term = max(0.0, float(mom) * 100.0)
    else:
        edge = float(0.5 - fp)
        mom_term = max(0.0, -float(mom) * 100.0)
    if edge <= 0:
        return None
    decisive_w = max(0.15, min(1.6, vol_band / 1.0))
    recent_w = max(0.15, min(1.6, vol_band_short / 0.7))
    hz_bonus = 1.08 if horizon_s <= PREFER_HZ_S else 1.0
    edge_term = 0.35 + 1.4 * min(edge, 0.25)
    score = edge_term * decisive_w * recent_w * hz_bonus + 0.06 * mom_term
    score *= fleet_priors.hf_pair_dir_mult(pair, direction)
    score *= fleet_priors.hf_hour_mult(now)
    return {
        "pair": pair,
        "direction": direction,
        "score": float(score),
        "edge": float(edge),
        "vol_band": float(vol_band),
        "vol_band_short": float(vol_band_short),
        "tp_bps": float(tp_bps),
        "horizon_s": int(horizon_s),
        "mom": float(mom),
    }


def rank_hf(pairs: list[str], now: float, cache: dict) -> list[dict]:
    board = hf_mod.hf_bands_as_of(now) or {}
    out = []
    for pair in pairs:
        row = board.get(pair)
        if not row:
            continue
        tp, _sl, horizon_s, _cls = row
        for d in ("LONG", "SHORT"):
            r = score_hf(pair, d, now, float(tp), int(horizon_s), cache)
            if r is not None:
                out.append(r)
    out.sort(key=lambda r: -r["score"])
    return out


def run(
    *,
    wallet_name: str,
    wallet_hotkey: str,
    pairs: list[str],
    pick_rank: int,
    poll_s: float,
) -> None:
    w = bt.Wallet(name=wallet_name, hotkey=wallet_hotkey)
    hk = w.hotkey.ss58_address
    cache: dict = {}
    last_bar_refresh = 0.0
    _log(
        {
            "event": "start",
            "mode": "hf_auto",
            "hotkey": hk,
            "pick_rank": pick_rank,
            "pairs": pairs,
            "max_per_day": MAX_PER_DAY,
            "min_gap_s": MIN_GAP_S,
            "min_vol_band": MIN_VOL_BAND,
            "min_vol_band_short": MIN_VOL_BAND_SHORT,
            "min_score": MIN_SCORE,
            "min_edge": MIN_EDGE,
            "min_top_ratio": MIN_TOP_RATIO,
            "max_per_pair_per_day": MAX_PER_PAIR_PER_DAY,
            "poll_s_default": POLL_S,
            "bar_refresh_s": BAR_REFRESH_S,
            "free_best": FREE_BEST,
            "strict_mode": STRICT_MODE,
            "strict_both_dirs": STRICT_BOTH_DIRS,
            "strict_pair_dirs": sorted(f"{p}:{d}" for p, d in STRICT_PAIR_DIRS),
            "pair_lock_s": PAIR_LOCK_S,
            "sibling_logs": [str(p) for p in _sibling_hf_logs()],
            "lf_log": str(LF_LOG),
            "fleet_priors": fleet_priors.load_stats_note(),
            "hf_deny": sorted(f"{p}:{d}" for p, d in fleet_priors.HF_PAIR_DIR_DENY),
            "bars_age_min": round(_bars_age_min() or -1, 1),
        }
    )

    while True:
        if STOP.exists():
            _log({"event": "stop_file", "path": str(STOP)})
            return

        now = time.time()
        today = _submits_today(now)
        if len(today) >= MAX_PER_DAY:
            # sleep until next UTC day
            to_day = (_day_id(now) + 1) * 86_400 - now + 30
            _sleep(to_day, "hf_daily_cap")
            continue
        if today and (now - max(today)) < MIN_GAP_S:
            _sleep(MIN_GAP_S - (now - max(today)) + 1, "hf_min_gap")
            continue

        age = _bars_age_min()
        if now - last_bar_refresh >= BAR_REFRESH_S or (age is not None and age > BAR_MAX_AGE_MIN):
            try:
                _refresh_bars(pairs)
                last_bar_refresh = time.time()
                age = _bars_age_min()
            except Exception as e:  # noqa: BLE001
                _log(
                    {
                        "event": "bars_refresh_error",
                        "error": f"{type(e).__name__}: {e}",
                        "bars_age_min": round(age or -1, 1),
                    }
                )
                last_bar_refresh = now
            cache.clear()
        # Do not score/submit on badly stale bars after a failed refresh.
        if age is not None and age > BAR_MAX_AGE_MIN * 3:
            _sleep(poll_s, f"bars_stale age_min={age:.1f}")
            continue

        lf_locked = _lf_locked_pairs_for_hotkey(now)
        # Own pairs under 24h pair lock or still-open horizon (not horizon-only).
        own_open = _own_locked_pairs(now)
        sibling = _hf_sibling_live(now)
        blocked_pd = set(sibling)
        for p in lf_locked | own_open:
            blocked_pd.add((p, "LONG"))
            blocked_pd.add((p, "SHORT"))

        cache.clear()
        ranked = rank_hf(pairs, now, cache)
        board_best = float(ranked[0]["score"]) if ranked else 0.0
        pair_counts = _pair_counts_today(now)
        quality = [
            r
            for r in ranked
            if (r["pair"], r["direction"]) not in blocked_pd
            and r["pair"] not in lf_locked
            and r["pair"] not in own_open
            and (
                MAX_PER_PAIR_PER_DAY <= 0
                or pair_counts.get(r["pair"], 0) < MAX_PER_PAIR_PER_DAY
            )
            and _passes_quality(
                r, board_best=board_best, now=now, pick_rank=pick_rank
            )
        ]
        # Free-best (default): claims + sibling locks already anti-copy; do not
        # hard-slice by pick_rank (that left h3/h4 at 0 when n_quality≈1).
        # Legacy slice kept only if SN89_HF_FREE_BEST=0.
        if FREE_BEST or pick_rank <= 0:
            free = quality
        else:
            preferred = quality[pick_rank:]
            free = preferred if preferred else quality
        free = _prefer_minority_side(free, now)

        target = None
        for r in free:
            if not _try_claim(
                r["pair"],
                r["direction"],
                hotkey=hk,
                now=now,
                horizon_s=r["horizon_s"],
            ):
                _log(
                    {
                        "event": "claim_busy",
                        "pair": r["pair"],
                        "direction": r["direction"],
                    }
                )
                continue
            target = r
            break

        if target is None:
            top = ranked[0] if ranked else None
            _sleep(
                poll_s,
                (
                    f"wait_quality n_ranked={len(ranked)} n_quality={len(quality)} "
                    f"board_best={board_best:.3f} "
                    f"top={top['pair']+':'+top['direction'] if top else '-'} "
                    f"lf_locked={sorted(lf_locked)[:4]} own_open={sorted(own_open)[:4]}"
                ),
            )
            continue

        pair, direction = target["pair"], target["direction"]
        _log(
            {
                "event": "submit_attempt",
                "pair": pair,
                "direction": direction,
                "score": round(target["score"], 4),
                "vol_band": round(target["vol_band"], 3),
                "vol_band_short": round(target["vol_band_short"], 3),
                "tp_bps": target["tp_bps"],
                "horizon_s": target["horizon_s"],
                "pick_rank": pick_rank,
                "n_today": len(today),
            }
        )
        try:
            resp = submit_hf(w, pair, direction)
        except Exception as e:  # noqa: BLE001
            # Ambiguous: ingest may have accepted. Hold pair lock + min gap.
            _log(
                {
                    "event": "submit_ambiguous",
                    "ok": False,
                    "pair": pair,
                    "direction": direction,
                    "horizon_s": target["horizon_s"],
                    "error": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc()[-600:],
                }
            )
            st = _load_state()
            st["last_submit"] = {
                "pair": pair,
                "direction": direction,
                "ts": time.time(),
                "ok": False,
                "ambiguous": True,
            }
            _save_state(st)
            _sleep(MIN_GAP_S, "hf_submit_ambiguous_gap")
            continue

        ok = resp.get("kind") == "hf.receipt" and resp.get("verified", True)
        if ok:
            _log(
                {
                    "event": "submit_ok",
                    "ok": True,
                    "pair": pair,
                    "direction": direction,
                    "horizon_s": target["horizon_s"],
                    "score": target["score"],
                    "vol_band": target["vol_band"],
                    "receipt_kind": resp.get("kind"),
                    "seq": resp.get("seq"),
                }
            )
            st = _load_state()
            st["last_submit"] = {
                "pair": pair,
                "direction": direction,
                "ts": time.time(),
                "ok": True,
            }
            _save_state(st)
            _sleep(MIN_GAP_S, "post_hf_submit")
        else:
            # Fail responses can still mean the call landed — treat as lock+gap.
            _log(
                {
                    "event": "submit_ambiguous",
                    "ok": False,
                    "pair": pair,
                    "direction": direction,
                    "horizon_s": target["horizon_s"],
                    "score": target["score"],
                    "receipt_kind": resp.get("kind"),
                    "seq": resp.get("seq"),
                    "error": resp.get("error") or resp.get("reason"),
                }
            )
            st = _load_state()
            st["last_submit"] = {
                "pair": pair,
                "direction": direction,
                "ts": time.time(),
                "ok": False,
                "ambiguous": True,
            }
            _save_state(st)
            _sleep(MIN_GAP_S, "hf_submit_ambiguous_gap")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SN89 HF auto-submit (concurrent with LF)")
    p.add_argument("--wallet.name", dest="wallet_name", default="rabi")
    p.add_argument("--wallet.hotkey", dest="wallet_hotkey", default="rabi-h1")
    p.add_argument(
        "--pairs",
        default=os.getenv("SN89_HF_AUTO_PAIRS", ",".join(DEFAULT_PAIRS)),
    )
    p.add_argument(
        "--pick-rank",
        type=int,
        default=int(os.getenv("SN89_HF_PICK_RANK", "0")),
    )
    p.add_argument(
        "--poll-s",
        type=float,
        default=float(os.getenv("SN89_HF_POLL_S", "45")),
    )
    args = p.parse_args(argv)
    pairs = [x.strip().upper() for x in args.pairs.split(",") if x.strip()]
    run(
        wallet_name=args.wallet_name,
        wallet_hotkey=args.wallet_hotkey,
        pairs=pairs,
        pick_rank=max(0, int(args.pick_rank)),
        poll_s=float(args.poll_s),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
