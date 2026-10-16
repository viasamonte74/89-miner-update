#!/usr/bin/env python3
"""LF auto-submitter: pick up to 3 best chances per UTC day (not first-3-hours).

Strategy
--------
Split the UTC day into 3 equal windows (00–08, 08–16, 16–24). In each window,
score crypto pairs on a rolling basis and remember the best LONG candidate.
Submit that candidate when:
  • conviction is high early (score ≥ early threshold), or
  • the window is ending (finalize), or
  • end-of-day force (must spend remaining slots before midnight + 1h gaps).

Still enforces ≤3/UTC day and ≥1h gap via the miner local guard.
"""
from __future__ import annotations

import argparse
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
    realized_stats,
)
from miner_ext.data.paths import DATA_ROOT
from neurons.miner import (
    ValidationError,
    build_signal,
    check_local_limits,
    record_local_submit,
    submit,
)
from sn89_signals import config
from sn89_signals import hf as hf_mod

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import fleet_priors  # noqa: E402
import network_live  # noqa: E402

DEFAULT_PAIRS = [
    # Top-miner cores (UID162 ZRO / UID80 TAO) + low-wash decisive alts
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
    "XMRUSD",
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
    # Majors last — chronic wash vs band (soft-penalized below)
    "ETHUSD",
    "BTCUSD",
]
LOG = Path(os.environ.get("SN89_AUTO_LOG", "/workspace/logs/lf_auto_submit.jsonl"))
HF_LOG = Path(
    os.environ.get("SN89_HF_AUTO_LOG", "/workspace/logs/hf_auto_submit.jsonl")
)
HF_CLAIM_DIR = Path(
    os.environ.get("SN89_HF_CLAIM_DIR", "/workspace/sn89_data/hf_fleet_claims")
)
PAIR_LOCK_S = float(os.environ.get("SN89_PAIR_LOCK_S", str(hf_mod.PAIR_LOCK_S)))
STOP = Path(os.environ.get("SN89_AUTO_STOP", "/workspace/sn89_data/STOP_AUTO_SUBMIT"))
STATE = Path(
    os.environ.get("SN89_AUTO_STATE", os.path.expanduser("~/.sn89/lf_auto_state.json"))
)
BARS_DIR = os.path.join(DATA_ROOT, "bars_1m")
N_WINDOWS = 3
WINDOW_S = 86_400 // N_WINDOWS  # 8h
# Rebuild 1m bars from archive this often so scores are not stuck on stale parquet.
BAR_REFRESH_S = float(os.environ.get("SN89_AUTO_BAR_REFRESH_S", "900"))
# Crypto LF horizon — used to treat sibling submits as still "live" for anti-copy.
SIBLING_LIVE_S = float(os.environ.get("SN89_AUTO_SIBLING_LIVE_S", str(8 * 3600)))
# Cross-process claims so h1/h2/h3 never race onto the same (pair, direction).
CLAIM_DIR = Path(
    os.environ.get("SN89_AUTO_CLAIM_DIR", "/workspace/sn89_data/fleet_claims")
)
# Stagger finalize so lower ranks claim after higher ranks (seconds per pick_rank).
FINALIZE_STAGGER_S = float(os.environ.get("SN89_AUTO_FINALIZE_STAGGER_S", "900"))
# Short-horizon vol/band gates (12d backtest: strongest decisive predictor).
MIN_VOL_BAND_2H_EARLY = float(os.environ.get("SN89_AUTO_MIN_VOL_BAND_2H_EARLY", "0.70"))
MIN_VOL_BAND_2H_SUBMIT = float(os.environ.get("SN89_AUTO_MIN_VOL_BAND_2H_SUBMIT", "0.65"))
# Cap same-direction fires per UTC day (anti-tilt; grades arrive too late to use W/L).
MAX_SAME_DIR_PER_DAY = int(os.environ.get("SN89_AUTO_MAX_SAME_DIR_PER_DAY", "2"))
# Do not burn end-of-day / catch-up slots on weak candidates — skip rather than wash.
SKIP_WEAK_EOD = os.environ.get("SN89_AUTO_SKIP_WEAK_EOD", "1").strip() not in (
    "0",
    "false",
    "False",
)
# Quality-only: never force fills via finalize / eod_force / catch_up.
# 0–1 LF calls/day is fine — forced ~50% slots only add Wilson noise.
QUALITY_ONLY = os.environ.get("SN89_LF_QUALITY_ONLY", "1").strip() not in (
    "0",
    "false",
    "False",
)
# Strict qualify mode: optional proven-pair filter + higher early score.
# SHORT-only bias defaults OFF — h2/h3 ran 4W/15L under prefer-short.
STRICT_MODE = os.environ.get("SN89_STRICT_MODE", "0").strip() not in (
    "0",
    "false",
    "False",
)
_STRICT_LF_PAIRS_RAW = os.environ.get(
    "SN89_STRICT_LF_PAIRS",
    "SUIUSD,XRPUSD,BCHUSD,DOGEUSD,ZROUSD,NEARUSD,DOTUSD,ALGOUSD,LTCUSD",
)
STRICT_LF_PAIRS = frozenset(
    x.strip().upper() for x in _STRICT_LF_PAIRS_RAW.split(",") if x.strip()
)
STRICT_EARLY_SCORE = float(os.environ.get("SN89_STRICT_EARLY_SCORE", "0.85"))
STRICT_PREFER_SHORT = os.environ.get("SN89_STRICT_PREFER_SHORT", "0").strip() not in (
    "0",
    "false",
    "False",
)
# Quality floor that treats LONG/SHORT the same (replaces SHORT-only strict).
LF_QUALITY_FLOOR = float(os.environ.get("SN89_LF_QUALITY_FLOOR", "0.0"))
# Down-weight high-wash pairs (h1 washed heavily on BTCUSD). Env CSV of pairs.
_LF_WASH_AVOID_RAW = os.environ.get("SN89_LF_WASH_AVOID_PAIRS", "BTCUSD")
LF_WASH_AVOID_PAIRS = frozenset(
    x.strip().upper() for x in _LF_WASH_AVOID_RAW.split(",") if x.strip()
)
LF_WASH_AVOID_MULT = float(os.environ.get("SN89_LF_WASH_AVOID_MULT", "0.35"))
LF_WASH_AVOID = os.environ.get("SN89_LF_WASH_AVOID", "0").strip() not in (
    "0",
    "false",
    "False",
)
BLOCK_SIBLING_OPPOSITE = os.environ.get(
    "SN89_LF_BLOCK_SIBLING_OPPOSITE", "1"
).strip() not in ("0", "false", "False")
BAR_MAX_AGE_H = float(os.environ.get("SN89_LF_BAR_MAX_AGE_H", "3.0"))
# Base priors (network / top-miner) then overlaid by fleet_priors (our book).
_PAIR_SCORE_MULT = {
    "BTCUSD": float(os.environ.get("SN89_AUTO_BTC_MULT", "0.20")),
    "ETHUSD": float(os.environ.get("SN89_AUTO_ETH_MULT", "0.30")),
    "SOLUSD": float(os.environ.get("SN89_AUTO_SOL_MULT", "0.70")),
    "HYPEUSD": 0.70,
    "ARBUSD": 0.60,
    "DOGEUSD": 0.85,
    "XRPUSD": 0.85,
    "ZROUSD": 1.35,
    "TAOUSD": 1.30,
    "ALGOUSD": 1.25,
    "LTCUSD": 1.22,
    "UNIUSD": 1.20,
    "BCHUSD": 1.18,
    "NEARUSD": 1.18,
    "SUIUSD": 1.12,
    "WLDUSD": 1.12,
    "AVAXUSD": 1.08,
    "ENAUSD": 1.08,
    "KPEPEUSD": 1.00,
}
# Fleet empirical overlay only in legacy priors mode (noisy small-n bans/boosts).
if fleet_priors.PRIORS_MODE == "legacy":
    for _p, _m in fleet_priors.LF_PAIR_MULT.items():
        _PAIR_SCORE_MULT[_p] = float(_m)
# Keep explicit env BTC/ETH soft-down regardless (wash control, not HR prior).
if "SN89_AUTO_BTC_MULT" in os.environ:
    _PAIR_SCORE_MULT["BTCUSD"] = float(os.environ["SN89_AUTO_BTC_MULT"])
if "SN89_AUTO_ETH_MULT" in os.environ:
    _PAIR_SCORE_MULT["ETHUSD"] = float(os.environ["SN89_AUTO_ETH_MULT"])


def _sibling_log_paths() -> list[Path]:
    """Other miners' submit jsonl files (anti-copy diversification)."""
    raw = os.environ.get("SN89_AUTO_SIBLING_LOGS", "").strip()
    if not raw:
        return []
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


def _expand_opposite(live: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """While a sibling holds a pair, block both directions (no cross-key hedge)."""
    if not BLOCK_SIBLING_OPPOSITE:
        return live
    out = set(live)
    for pair, _d in list(live):
        out.add((pair, "LONG"))
        out.add((pair, "SHORT"))
    return out


def _sibling_live_trades(now: float) -> set[tuple[str, str]]:
    """{(pair, direction)} still live on a sibling hotkey (local log heuristic)."""
    live: set[tuple[str, str]] = set()
    cutoff = now - SIBLING_LIVE_S
    for path in _sibling_log_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("event") not in {"submit_ok", "submit_ambiguous"}:
                continue
            if o.get("event") == "submit_ok" and not o.get("ok", True):
                continue
            ts = float(o.get("ts") or 0.0)
            if ts < cutoff:
                continue
            pair = o.get("pair")
            direction = o.get("direction")
            if pair and direction:
                live.add((str(pair).upper(), str(direction).upper()))
    return _expand_opposite(live)


def _live_pairs_from_submit_log(
    path: Path, now: float, *, by_pair_only: bool, live_s: float | None = None
) -> set:
    """Pairs (or (pair, dir)) still live per submit_ok / submit_ambiguous lines."""
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
        if o.get("event") not in {"submit_ok", "submit_ambiguous"}:
            continue
        if o.get("event") == "submit_ok" and not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0)
        if live_s is not None:
            if now - ts > live_s:
                continue
        else:
            # Open-position / sibling window only. Cross-mech 24h lock uses
            # live_s=PAIR_LOCK_S at the call site — do not bake it into default.
            hz = float(o.get("horizon_s") or 8 * 3600)
            if now - ts > hz:
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


def _hf_locked_pairs_for_lf(now: float) -> set[str]:
    """Pairs this hotkey must not LF-submit (24h cross-mechanism lock + open HF).

    Mirrors validator `is_pair_locked` for mecid LF: any HF submit on the pair
    within PAIR_LOCK_S blocks LF. Also blocks while an HF call is still open.
    """
    locked = _live_pairs_from_submit_log(
        HF_LOG, now, by_pair_only=True, live_s=PAIR_LOCK_S
    )
    locked |= _live_pairs_from_submit_log(HF_LOG, now, by_pair_only=True)
    if HF_CLAIM_DIR.exists():
        try:
            our_hf = str(HF_LOG.resolve())
        except OSError:
            our_hf = str(HF_LOG)
        for path in HF_CLAIM_DIR.glob("*.json"):
            try:
                o = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if float(o.get("until") or 0) <= now:
                continue
            log_s = str(o.get("log") or "")
            if log_s and (log_s == our_hf or log_s == str(HF_LOG)):
                locked.add(str(o.get("pair") or "").upper())
    return {p for p in locked if p}


def _fleet_claimed_trades(now: float, *, self_hotkey: str | None = None) -> set[tuple[str, str]]:
    """{(pair, direction)} reserved by another fleet miner (atomic claims)."""
    live: set[tuple[str, str]] = set()
    if not CLAIM_DIR.exists():
        return live
    for path in CLAIM_DIR.glob("*.json"):
        try:
            o = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        until = float(o.get("until") or 0.0)
        if until <= now:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        if self_hotkey and o.get("hotkey") == self_hotkey:
            continue
        pair = o.get("pair")
        direction = o.get("direction")
        if pair and direction:
            live.add((str(pair).upper(), str(direction).upper()))
    return _expand_opposite(live)


def _fleet_ss58() -> set[str]:
    """Our fleet hotkeys (ss58) — never treat as external copy leaders."""
    raw = os.environ.get("SN89_FLEET_HOTKEYS", "").strip()
    if raw:
        return {x.strip() for x in raw.split(",") if x.strip()}
    # Best-effort: wallet names rabi-h1..h4 under SN89_WALLET_NAME
    out: set[str] = set()
    name = os.environ.get("SN89_WALLET_NAME", "rabi")
    for hk in ("rabi-h1", "rabi-h2", "rabi-h3", "rabi-h4"):
        try:
            out.add(bt.Wallet(name=name, hotkey=hk).hotkey.ss58_address)
        except Exception:  # noqa: BLE001
            continue
    return out


def _network_blocked_trades(
    now: float, *, force: bool = False
) -> set[tuple[str, str]]:
    """Block (pair,dir) already pending on any non-fleet hotkey (anti-copy)."""
    if os.environ.get("SN89_NETWORK_LIVE_BLOCK", "1").strip() in (
        "0",
        "false",
        "False",
    ):
        return set()
    return network_live.network_live_trades(
        now, exclude_hotkeys=_fleet_ss58(), force=force
    )


def _blocked_trades(
    now: float, *, hotkey: str, relax_dir_tilt: bool = False
) -> set[tuple[str, str]]:
    blocked = _sibling_live_trades(now) | _fleet_claimed_trades(
        now, self_hotkey=hotkey
    )
    blocked |= _network_blocked_trades(now)
    for pair in _hf_locked_pairs_for_lf(now):
        blocked.add((pair, "LONG"))
        blocked.add((pair, "SHORT"))
    blocked |= _dir_tilt_blocked(now, relax_global=relax_dir_tilt)
    return blocked


def _release_claim(pair: str, direction: str, *, hotkey: str) -> None:
    """Drop our fleet claim if we abort before / after a failed submit."""
    key = f"{pair.upper()}_{direction.upper()}"
    path = CLAIM_DIR / f"{key}.json"
    if not path.exists():
        return
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if o.get("hotkey") == hotkey:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _try_claim_trade(
    pair: str,
    direction: str,
    *,
    hotkey: str,
    now: float,
) -> bool:
    """Exclusively reserve (pair, direction) for this hotkey until horizon end.

    Returns False if another fleet miner already holds a live claim.
    """
    CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{pair.upper()}_{direction.upper()}"
    path = CLAIM_DIR / f"{key}.json"
    lock_path = CLAIM_DIR / f"{key}.lock"
    payload = {
        "pair": pair.upper(),
        "direction": direction.upper(),
        "hotkey": hotkey,
        "ts": now,
        "until": now + SIBLING_LIVE_S,
        "log": str(LOG),
    }
    import fcntl

    lock_path.touch(exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    existing = {}
                until = float(existing.get("until") or 0.0)
                owner = existing.get("hotkey")
                if until > now and owner and owner != hotkey:
                    return False
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, path)
            return True
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _iter_actionable(
    ranked: list[dict],
    *,
    early: bool,
    early_score: float,
    min_vol_band_early: float,
    min_vol_band_submit: float,
    min_vol_band_2h_early: float = MIN_VOL_BAND_2H_EARLY,
    min_vol_band_2h_submit: float = MIN_VOL_BAND_2H_SUBMIT,
    now: float | None = None,
):
    """Yield gate-passing candidates in rank order (for claim cascade)."""
    for r in ranked:
        pair = str(r.get("pair") or "").upper()
        if fleet_priors.lf_pair_blocked(pair):
            continue
        vol = float(r.get("vol_band") or 0.0)
        vol2 = float(r.get("vol_band_2h") or 0.0)
        if vol < min_vol_band_submit or vol2 < min_vol_band_2h_submit:
            continue
        score = float(r["score"])
        if early:
            if (
                score < early_score
                or vol < min_vol_band_early
                or vol2 < min_vol_band_2h_early
            ):
                continue
        if now is not None and fleet_priors.lf_hour_blocked(
            now, score=score, score_floor=early_score
        ):
            continue
        yield r


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


def _window_index(now: float) -> int:
    return int((now % 86_400) // WINDOW_S)


def _window_start(now: float) -> float:
    d = _day_id(now)
    w = _window_index(now)
    return d * 86_400 + w * WINDOW_S


def _window_end(now: float) -> float:
    d = _day_id(now)
    w = _window_index(now)
    return d * 86_400 + (w + 1) * WINDOW_S


def _seconds_until_next_utc_day(now: float) -> float:
    return max(5.0, (_day_id(now) + 1) * 86_400 - now + 30.0)


def _seconds_until_gap_clear(hotkey: str, now: float) -> float:
    _, gap = config.submission_rules_as_of(now)
    if not gap:
        return 0.0
    from neurons.miner import _load_submit_log

    ts = _load_submit_log(hotkey)
    if not ts:
        return 0.0
    since = now - max(ts)
    if since >= gap:
        return 0.0
    return float(gap - since) + 5.0


def _submits_today(hotkey: str, now: float) -> int:
    from neurons.miner import _load_submit_log

    day = _day_id(now)
    return sum(1 for t in _load_submit_log(hotkey) if _day_id(t) == day)


def _directions_today(now: float) -> dict[str, int]:
    """Count submit_ok directions in this process log for the UTC day."""
    counts: dict[str, int] = {"LONG": 0, "SHORT": 0}
    if not LOG.exists():
        return counts
    day = _day_id(now)
    try:
        text = LOG.read_text(encoding="utf-8")
    except OSError:
        return counts
    for line in text.splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0.0)
        if _day_id(ts) != day:
            continue
        d = str(o.get("direction") or "").upper()
        if d in counts:
            counts[d] += 1
    return counts


def _pair_dirs_today(now: float) -> dict[tuple[str, str], int]:
    """Count submit_ok (pair, direction) in this process log for the UTC day."""
    counts: dict[tuple[str, str], int] = {}
    if not LOG.exists():
        return counts
    day = _day_id(now)
    try:
        text = LOG.read_text(encoding="utf-8")
    except OSError:
        return counts
    for line in text.splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("event") != "submit_ok" or not o.get("ok", True):
            continue
        ts = float(o.get("ts") or 0.0)
        if _day_id(ts) != day:
            continue
        pair = str(o.get("pair") or "").upper()
        d = str(o.get("direction") or "").upper()
        if pair and d in ("LONG", "SHORT"):
            key = (pair, d)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _dir_tilt_blocked(
    now: float, *, relax_global: bool = False
) -> set[tuple[str, str]]:
    """Anti-tilt blocks.

    Always: no exact (pair, dir) repeat the same UTC day.
    Soft global: once a direction hits MAX_SAME_DIR_PER_DAY, block that dir on
    all pairs — but callers may pass relax_global=True so a hotkey can still
    fill an empty LF slot instead of starving (h2 failure mode).
    """
    blocked: set[tuple[str, str]] = set()
    for (pair, d), n in _pair_dirs_today(now).items():
        if n >= 1:
            blocked.add((pair, d))
    if relax_global or MAX_SAME_DIR_PER_DAY <= 0:
        return blocked
    counts = _directions_today(now)
    for d, n in counts.items():
        if n >= MAX_SAME_DIR_PER_DAY:
            for pair in DEFAULT_PAIRS:
                blocked.add((pair, d))
    return blocked


def _install_ws_keepalive_silence() -> None:
    """Subtensor opens a sync websocket whose keepalive thread dumps a
    ConnectionClosedError traceback if the socket dies during long sleeps.
    Harmless for the miner loop — silence that thread only."""
    import threading

    if getattr(_install_ws_keepalive_silence, "_done", False):
        return
    prev = threading.excepthook

    def _hook(args):  # type: ignore[no-untyped-def]
        exc = args.exc_value
        name = type(exc).__name__ if exc is not None else ""
        mod = type(exc).__module__ if exc is not None else ""
        msg = str(exc) if exc is not None else ""
        if (
            "websockets" in mod
            or name in ("ConnectionClosedError", "ConnectionClosedOK")
            or "keepalive ping" in msg
        ):
            try:
                _log(
                    {
                        "event": "ws_keepalive_closed",
                        "error": f"{name}: {msg}"[:200],
                    }
                )
            except Exception:
                pass
            return
        if prev is not None:
            prev(args)
        else:
            threading.__excepthook__(args)

    threading.excepthook = _hook
    _install_ws_keepalive_silence._done = True  # type: ignore[attr-defined]


def _sleep(sec: float, reason: str) -> None:
    sec = max(1.0, float(sec))
    _install_ws_keepalive_silence()
    _log({"event": "sleep", "seconds": round(sec, 1), "reason": reason})
    end = time.time() + sec
    while time.time() < end:
        if STOP.exists():
            _log({"event": "stop_file", "path": str(STOP)})
            raise SystemExit(0)
        time.sleep(min(30.0, end - time.time()))


def _bars_age_h(pair: str = "BTCUSD") -> float | None:
    """Hours since last bar timestamp (not file mtime). None if missing/unreadable."""
    path = os.path.join(BARS_DIR, f"{pair}.parquet")
    if not os.path.isfile(path):
        return None
    try:
        t = pq.read_table(path, columns=["t"]).column("t").to_numpy()
        if len(t) == 0:
            return None
        return (time.time() * 1000.0 - float(t[-1])) / 3_600_000.0
    except Exception:  # noqa: BLE001
        return None


def _refresh_bars(pairs: list[str]) -> None:
    """Rebuild 1m bars; flock so fleet LF/HF processes do not thrash together."""
    import fcntl

    from miner_ext.backtest.bars import build_bars

    data_root = os.environ.get("SN89_DATA_ROOT", "/workspace/sn89_data")
    lock_path = Path(data_root) / "bars_refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    _log({"event": "bars_refresh_start", "n_pairs": len(pairs)})
    with lock_path.open("a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            build_bars(pairs=pairs)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    _log(
        {
            "event": "bars_refresh_done",
            "wall_s": round(time.time() - t0, 1),
            "bars_age_h": round(_bars_age_h() or -1, 3),
        }
    )


def _load_close(pair: str) -> tuple[np.ndarray, np.ndarray] | None:
    path = os.path.join(BARS_DIR, f"{pair}.parquet")
    if not os.path.isfile(path):
        return None
    try:
        tbl = pq.read_table(path, columns=["t", "c"])
        t = tbl.column("t").to_numpy()
        c = tbl.column("c").to_numpy()
        return t.astype(np.int64), c.astype(np.float64)
    except Exception:  # noqa: BLE001
        return None


def score_candidate(
    pair: str,
    direction: str,
    now: float,
    band_bps: float,
    cache: dict[str, tuple[np.ndarray, np.ndarray]],
) -> dict | None:
    """Score for decisive TP/SL likelihood first, then mild directional edge.

    12d bar study: short-horizon vol/band (2h) predicts decisive far better than
    edge alone; high edge with quiet tape → washes. Prefer vol_band_8h ≳ 1.2 and
    vol_band_2h ≳ 0.5 (recent expansion into the band).
    """
    if fleet_priors.lf_pair_blocked(pair):
        return None
    if pair not in cache:
        series = _load_close(pair)
        if series is None:
            return None
        cache[pair] = series
    t_ms, close = cache[pair]
    decision_ms = int(now * 1000)
    stats = realized_stats(t_ms, close, decision_ms, lookback_min=1440)
    stats_2h = realized_stats(t_ms, close, decision_ms, lookback_min=120)
    if stats is None or stats_2h is None:
        return None
    _, mu, sig = stats
    _, _, sig_2h = stats_2h
    if sig <= 0 or sig_2h <= 0 or band_bps <= 0:
        return None
    fp = first_passage_p_long(mu, sig, band_bps)
    band_frac = band_bps / 10_000.0
    # 8h horizon ≈ 480 one-minute steps; 2h ≈ 120
    vol_band = float(sig) * (480.0 ** 0.5) / band_frac
    vol_band_2h = float(sig_2h) * (120.0 ** 0.5) / band_frac
    # Saturating weights from backtest buckets
    decisive_w = max(0.15, min(1.6, vol_band / 1.0))
    recent_w = max(0.15, min(1.6, vol_band_2h / 0.55))

    if direction == "LONG":
        edge = float(fp - 0.5)
    else:
        edge = float(0.5 - fp)

    if edge <= 0.0:
        return None  # never submit against the drift edge

    # Cap edge: large edge without vol was anti-predictive for decisive in study.
    # No mom_term — trend bonus is the wrong sign; LF rules flip halves anyway.
    edge_term = 0.40 + 1.5 * min(edge, 0.22)
    score = edge_term * decisive_w * recent_w
    if LF_QUALITY_FLOOR > 0 and score < LF_QUALITY_FLOOR:
        return None
    score *= _PAIR_SCORE_MULT.get(pair, 1.0)
    if LF_WASH_AVOID and pair.upper() in LF_WASH_AVOID_PAIRS:
        score *= LF_WASH_AVOID_MULT
    score *= fleet_priors.lf_hour_mult(now)
    return {
        "pair": pair,
        "direction": direction,
        "score": float(score),
        "fp_p_long": float(fp),
        "mom_60": 0.0,
        "edge": float(edge),
        "vol_band": float(vol_band),
        "vol_band_2h": float(vol_band_2h),
        "decisive_w": float(decisive_w),
        "recent_w": float(recent_w),
        "band_bps": float(band_bps),
    }


def rank_universe(
    pairs: list[str],
    direction: str,
    now: float,
    cache: dict,
) -> list[dict]:
    board = config.allowed_assets_as_of(now)
    dirs = ("LONG", "SHORT") if direction == "AUTO" else (direction,)
    out = []
    use_pairs = pairs
    if STRICT_MODE and STRICT_LF_PAIRS:
        use_pairs = [p for p in pairs if p.upper() in STRICT_LF_PAIRS]
    for pair in use_pairs:
        meta = board.get(pair)
        if not meta or meta.get("asset_class") != "crypto":
            continue
        band = float(meta["tp_bps"])
        for d in dirs:
            if STRICT_MODE and STRICT_PREFER_SHORT and d == "LONG":
                # Fleet LF LONG HR ~20% vs SHORT ~57% — skip LONGs in strict mode.
                continue
            row = score_candidate(pair, d, now, band, cache)
            if row is not None:
                out.append(row)
    out.sort(key=lambda r: -r["score"])
    return out


def _ensure_day_state(st: dict, now: float) -> dict:
    day = _day_id(now)
    if st.get("day_id") != day:
        st = {
            "day_id": day,
            "filled_windows": [],
            "window_best": {},
        }
    st.setdefault("filled_windows", [])
    st.setdefault("window_best", {})
    return st


def _filter_ranked(
    ranked: list[dict],
    *,
    pick_rank: int,
    blocked: set[tuple[str, str]],
) -> list[dict]:
    """Drop blocked (pair,dir). pick_rank prefers lower board slots but must not
    starve: if the preferred slice is empty, fall back to all free candidates.

    (Old behavior sliced free[pick_rank:] *after* removing sibling takes, which
    double-penalized h2/h3 and could empty the board.)
    """
    free = [
        r
        for r in ranked
        if (r["pair"], r["direction"]) not in blocked
    ]
    if pick_rank <= 0 or not free:
        return free
    preferred = free[pick_rank:]
    return preferred if preferred else free


def _first_actionable(
    ranked: list[dict],
    *,
    early: bool,
    early_score: float,
    min_vol_band_early: float,
    min_vol_band_submit: float,
    now: float | None = None,
) -> dict | None:
    """From this miner's rank-slice, take the first candidate that clears gates."""
    for r in _iter_actionable(
        ranked,
        early=early,
        early_score=early_score,
        min_vol_band_early=min_vol_band_early,
        min_vol_band_submit=min_vol_band_submit,
        now=now,
    ):
        return r
    return None


def _pick_submit_target(st: dict, window: int, ranked: list[dict]) -> dict | None:
    """Preferred watch candidate for this window (top of rank-slice)."""
    if not ranked:
        return None
    wb = st.get("window_best", {}).get(str(window))
    if wb and ranked:
        # refresh if live best beats stored — stored must still be in free set
        best = ranked[0]
        same = (
            wb.get("pair") == best["pair"]
            and wb.get("direction") == best["direction"]
        )
        if same or best["score"] > float(wb.get("score", -1e9)):
            return best
        # keep stored only if it is still an allowed free candidate
        for r in ranked:
            if r["pair"] == wb.get("pair") and r["direction"] == wb.get("direction"):
                return {**wb, **{k: r[k] for k in r}}
        return best
    return ranked[0]


def run(
    *,
    wallet_name: str,
    wallet_hotkey: str,
    direction: str,
    pairs: list[str],
    poll_s: float,
    early_score: float,
    finalize_min: float,
    min_vol_band_early: float,
    min_vol_band_submit: float,
    early_min_elapsed_min: float,
    pick_rank: int,
) -> None:
    if not (os.getenv("SN89_FEED_TOKEN") or os.getenv("SN89_RELAY_TOKEN")):
        _log({"event": "fatal", "error": "SN89_FEED_TOKEN not set"})
        raise SystemExit(2)

    w = bt.Wallet(name=wallet_name, hotkey=wallet_hotkey)
    hk = w.hotkey.ss58_address
    if STRICT_MODE and early_score < STRICT_EARLY_SCORE:
        early_score = STRICT_EARLY_SCORE
    _install_ws_keepalive_silence()
    st = _ensure_day_state(_load_state(), time.time())
    cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    last_bar_refresh = 0.0
    _log(
        {
            "event": "start",
            "mode": "best_of_day_windows",
            "hotkey": hk,
            "strict_mode": STRICT_MODE,
            "strict_lf_pairs": sorted(STRICT_LF_PAIRS) if STRICT_MODE else [],
            "strict_prefer_short": bool(STRICT_MODE and STRICT_PREFER_SHORT),
            "lf_quality_floor": LF_QUALITY_FLOOR,
            "lf_wash_avoid": LF_WASH_AVOID,
            "lf_wash_avoid_pairs": sorted(LF_WASH_AVOID_PAIRS) if LF_WASH_AVOID else [],
            "block_sibling_opposite": BLOCK_SIBLING_OPPOSITE,
            "direction": direction,
            "pairs": pairs,
            "poll_s": poll_s,
            "early_score": early_score,
            "finalize_min": finalize_min,
            "min_vol_band_early": min_vol_band_early,
            "min_vol_band_submit": min_vol_band_submit,
            "early_min_elapsed_min": early_min_elapsed_min,
            "pick_rank": pick_rank,
            "sibling_logs": [str(p) for p in _sibling_log_paths()],
            "hf_log": str(HF_LOG),
            "pair_lock_s": PAIR_LOCK_S,
            "max_same_dir_per_day": MAX_SAME_DIR_PER_DAY,
            "skip_weak_eod": SKIP_WEAK_EOD,
            "quality_only": QUALITY_ONLY,
            "fleet_priors": fleet_priors.load_stats_note(),
            "priors_mode": fleet_priors.PRIORS_MODE,
            "lf_deny_pairs": (
                sorted(fleet_priors.LF_PAIR_DENY)
                if fleet_priors.PRIORS_MODE == "legacy"
                else []
            ),
            "lf_hard_avoid_hours": (
                sorted(fleet_priors.LF_HARD_AVOID_HOURS)
                if fleet_priors.PRIORS_MODE == "legacy"
                else []
            ),
            "claim_dir": str(CLAIM_DIR),
            "finalize_stagger_s": FINALIZE_STAGGER_S,
            "btc_mult": _PAIR_SCORE_MULT.get("BTCUSD", 1.0),
            "bar_refresh_s": BAR_REFRESH_S,
            "n_windows": N_WINDOWS,
            "n_pairs": len(pairs),
            "bars_age_h": round(_bars_age_h() or -1, 3),
        }
    )

    while True:
        if STOP.exists():
            _log({"event": "stop_file", "path": str(STOP)})
            return

        now = time.time()
        # Keep 1m bars fresh from archive (root cause of stuck identical scores).
        age_h = _bars_age_h()
        if now - last_bar_refresh >= BAR_REFRESH_S or (age_h or 99) > 1.0:
            try:
                _refresh_bars(pairs)
                last_bar_refresh = time.time()
                age_h = _bars_age_h()
            except Exception as e:  # noqa: BLE001
                _log(
                    {
                        "event": "bars_refresh_error",
                        "error": f"{type(e).__name__}: {e}",
                        "bars_age_h": round(age_h or -1, 3),
                    }
                )
                last_bar_refresh = now  # avoid tight error loop
            cache.clear()
        if age_h is None or age_h > BAR_MAX_AGE_H:
            _sleep(
                poll_s,
                f"bars_stale_or_missing age_h={round(age_h, 2) if age_h is not None else None}",
            )
            continue

        st = _ensure_day_state(st, now)
        window = _window_index(now)
        w_end = _window_end(now)
        to_w_end = w_end - now
        to_day_end = (_day_id(now) + 1) * 86_400 - now
        n_today = _submits_today(hk, now)
        cap, gap = config.submission_rules_as_of(now)
        slots_left = max(0, int(cap) - n_today)
        # Map today's submits onto slot windows 0..n-1 so restarts don't double-fire
        st["filled_windows"] = list(range(min(n_today, N_WINDOWS)))
        filled = set(st["filled_windows"])

        # Always re-read parquet after possible refresh (no sticky in-memory series).
        cache.clear()
        raw_ranked = rank_universe(pairs, direction, now, cache)
        blocked = _blocked_trades(now, hotkey=hk)
        ranked = _filter_ranked(raw_ranked, pick_rank=pick_rank, blocked=blocked)
        # Dir-tilt starvation escape: if global same-dir cap emptied the board but
        # we still have LF slots, relax tilt (keep per-pair no-repeat + sibling/HF).
        if not ranked and slots_left > 0 and raw_ranked:
            blocked_relaxed = _blocked_trades(
                now, hotkey=hk, relax_dir_tilt=True
            )
            ranked_relaxed = _filter_ranked(
                raw_ranked, pick_rank=pick_rank, blocked=blocked_relaxed
            )
            if ranked_relaxed:
                _log(
                    {
                        "event": "dir_tilt_relaxed",
                        "dirs_today": _directions_today(now),
                        "slots_left": slots_left,
                        "n_free": len(ranked_relaxed),
                        "top": (
                            f"{ranked_relaxed[0]['pair']}:"
                            f"{ranked_relaxed[0]['direction']}"
                        ),
                    }
                )
                blocked = blocked_relaxed
                ranked = ranked_relaxed
        if ranked:
            best = ranked[0]
            prev = st["window_best"].get(str(window))
            if prev is None or best["score"] > float(prev.get("score", -1e9)):
                st["window_best"][str(window)] = {**best, "seen_at": now}
                _save_state(st)
                _log(
                    {
                        "event": "window_best",
                        "window": window,
                        "pair": best["pair"],
                        "direction": best["direction"],
                        "score": round(best["score"], 4),
                        "fp_p_long": round(best["fp_p_long"], 4),
                        "mom_60": round(best["mom_60"], 6),
                        "vol_band": round(best.get("vol_band", 0), 3),
                        "vol_band_2h": round(best.get("vol_band_2h", 0), 3),
                        "edge": round(best.get("edge", 0), 4),
                        "pick_rank": pick_rank,
                        "blocked": sorted(f"{p}:{d}" for p, d in blocked),
                        "slots_left": slots_left,
                        "to_window_end_min": round(to_w_end / 60.0, 1),
                        "bars_age_h": round(_bars_age_h() or -1, 3),
                    }
                )
        elif blocked and raw_ranked:
            _log(
                {
                    "event": "all_blocked_by_siblings",
                    "blocked": sorted(f"{p}:{d}" for p, d in blocked),
                    "dirs_today": _directions_today(now),
                    "top_raw": (
                        f"{raw_ranked[0]['pair']}:{raw_ranked[0]['direction']}"
                        if raw_ranked
                        else None
                    ),
                }
            )

        limit = check_local_limits(hk, now)
        if limit and limit[0] == "quota":
            _sleep(_seconds_until_next_utc_day(now), f"quota: {limit[1]}")
            continue
        if limit and limit[0] == "gap":
            # keep scanning scores while waiting out the gap
            wait = _seconds_until_gap_clear(hk, now)
            _sleep(min(wait or poll_s, poll_s), f"gap_scan: {limit[1]}")
            continue

        if slots_left <= 0:
            _sleep(_seconds_until_next_utc_day(now), "no_slots_left")
            continue

        missed = [i for i in range(window) if i not in filled]
        # Quality-only (default): never force slot fills. Forced ~50% LF only
        # adds wash noise to the Wilson bound.
        if QUALITY_ONLY:
            eod_force = False
            catch_up = False
            finalize = False
        else:
            eod_force = to_day_end <= slots_left * (float(gap or 3600) + 120.0)
            catch_up = bool(missed) and slots_left > 0
            finalize = False  # set below after we know window timing
        # already filled this window and not catching up / EOD → wait
        if window in filled and not catch_up and not eod_force:
            _sleep(min(poll_s, max(5.0, to_w_end)), "window_already_filled")
            continue

        # Hour bans only when priors mode enables them (default off).
        if fleet_priors.lf_hour_blocked(now, score=0.0, score_floor=early_score):
            _sleep(min(poll_s, 600.0), f"lf_hour_blocked h={fleet_priors.utc_hour(now)}")
            continue

        watch = _pick_submit_target(st, window, ranked)
        if watch is None:
            _sleep(poll_s, "no_scores_yet")
            continue

        # Per-pair freshness (BTC age alone can miss a quiet alt book).
        pair_age_h = _bars_age_h(str(watch.get("pair") or "BTCUSD"))
        if pair_age_h is None or pair_age_h > BAR_MAX_AGE_H:
            _sleep(
                poll_s,
                f"pair_bars_stale pair={watch.get('pair')} "
                f"age_h={round(pair_age_h, 2) if pair_age_h is not None else None}",
            )
            continue

        vol_band = float(watch.get("vol_band") or 0.0)
        elapsed_min = (now - _window_start(now)) / 60.0
        early_time = (window not in filled) and (
            elapsed_min >= early_min_elapsed_min
        )
        # Stagger finalize by pick_rank so h1 claims first, then h2, then h3.
        finalize_due = finalize_min * 60.0 + pick_rank * FINALIZE_STAGGER_S
        if not QUALITY_ONLY:
            finalize = (window not in filled) and (to_w_end <= finalize_due)

        if not (early_time or finalize or eod_force or catch_up):
            _sleep(
                poll_s,
                (
                    f"wait_better window={window} score={watch['score']:.3f} "
                    f"vol_band={vol_band:.2f} elapsed={elapsed_min:.0f}m "
                    f"need_score>={early_score} vol>={min_vol_band_early} "
                    f"elapsed>={early_min_elapsed_min}m"
                    + (
                        " quality_only"
                        if QUALITY_ONLY
                        else f" or finalize<{finalize_due/60:.0f}m"
                    )
                ),
            )
            continue

        # Refresh blocks right before choosing (siblings may have just submitted).
        # Preserve dir-tilt relax if that is what produced a non-empty board above.
        blocked = _blocked_trades(now, hotkey=hk)
        ranked = _filter_ranked(raw_ranked, pick_rank=pick_rank, blocked=blocked)
        if not ranked and slots_left > 0:
            blocked = _blocked_trades(now, hotkey=hk, relax_dir_tilt=True)
            ranked = _filter_ranked(
                raw_ranked, pick_rank=pick_rank, blocked=blocked
            )

        # Cascade + exclusive fleet claim: never submit a live sibling (pair,dir).
        # Quality-only: only the early (high-score) gate may fire.
        target = None
        early = False
        want_early = early_time
        phases: list[bool] = []
        if want_early:
            phases.append(True)
        if (not QUALITY_ONLY) and (finalize or eod_force or catch_up):
            # Weak EOD/catch-up/finalize: require early-quality gates (fewer washes).
            use_early_gates = bool(SKIP_WEAK_EOD) or want_early
            if use_early_gates not in phases:
                phases.append(use_early_gates)
        for use_early in phases:
            for cand in _iter_actionable(
                ranked,
                early=use_early,
                early_score=early_score,
                min_vol_band_early=min_vol_band_early,
                min_vol_band_submit=min_vol_band_submit,
                now=now,
            ):
                d_use = cand.get("direction") or (
                    "LONG" if direction == "AUTO" else direction
                )
                # Skip crowded network (pair,dir) before claiming — anti-copy.
                if (cand["pair"], d_use) in _network_blocked_trades(now):
                    _log(
                        {
                            "event": "skip_network_live",
                            "pair": cand["pair"],
                            "direction": d_use,
                            "phase": "pre_claim",
                        }
                    )
                    continue
                if not _try_claim_trade(
                    cand["pair"], d_use, hotkey=hk, now=now
                ):
                    _log(
                        {
                            "event": "claim_busy",
                            "pair": cand["pair"],
                            "direction": d_use,
                            "pick_rank": pick_rank,
                        }
                    )
                    continue
                target = cand
                early = use_early
                break
            if target is not None:
                break

        if target is None:
            _sleep(
                poll_s,
                (
                    f"skip_no_actionable watch={watch['pair']}:{watch['direction']} "
                    f"score={watch['score']:.3f} vol_band={vol_band:.2f} "
                    f"need_vol>={min_vol_band_submit} blocked={len(blocked)}"
                ),
            )
            continue

        pair = target["pair"]
        direction_use = target.get("direction") or (
            "LONG" if direction == "AUTO" else direction
        )
        if catch_up and not early and not finalize:
            reason = "catch_up"
            mark_window = missed[0]
        elif early:
            reason = "early_threshold"
            mark_window = window
        elif finalize:
            reason = "window_finalize"
            mark_window = window
        else:
            reason = "eod_force"
            mark_window = window
        try:
            sig = build_signal(hk, pair, direction_use, None, f"lf_auto:{reason}")
        except ValidationError as e:
            _log({"event": "skip_invalid", "pair": pair, "error": str(e)})
            _sleep(poll_s, "invalid_pair")
            continue

        # Fresh network check right before commit — avoid is_copy vs leaders.
        try:
            net_live = _network_blocked_trades(now, force=True)
        except RuntimeError as e:
            _release_claim(pair, direction_use, hotkey=hk)
            _log(
                {
                    "event": "skip_network_live_stale",
                    "pair": pair,
                    "direction": direction_use,
                    "reason": reason,
                    "error": str(e),
                    "phase": "pre_submit",
                }
            )
            _sleep(max(poll_s, 60.0), "network_live_stale")
            continue
        if (pair, direction_use) in net_live:
            _release_claim(pair, direction_use, hotkey=hk)
            _log(
                {
                    "event": "skip_network_live",
                    "pair": pair,
                    "direction": direction_use,
                    "reason": reason,
                    "score": target.get("score"),
                    "phase": "pre_submit",
                }
            )
            _sleep(poll_s, "network_live_copy_risk")
            continue

        _log(
            {
                "event": "submit_attempt",
                "reason": reason,
                "window": window,
                "pair": pair,
                "direction": direction_use,
                "score": target.get("score"),
                "fp_p_long": target.get("fp_p_long"),
                "vol_band": round(float(target.get("vol_band") or 0), 3),
                "edge": target.get("edge"),
                "pick_rank": pick_rank,
            }
        )
        # Explicit Chain so we can close the substrate websocket after commit.
        # Leaving it open lets the keepalive thread crash hours later during
        # quota sleep (ConnectionClosedError: keepalive ping timeout).
        from sn89_signals import chain as chain_mod

        ch = chain_mod.Chain()
        try:
            res = submit(w, sig, ch=ch)
        except Exception as e:  # noqa: BLE001
            # Ambiguous: extrinsic may have landed. Keep claim, burn local gap
            # slot, do NOT retry in 2–3 minutes (that voids the 1h gap / enables
            # sibling copy on the released pair).
            try:
                record_local_submit(hk)
            except Exception:  # noqa: BLE001
                pass
            st["last_submit"] = {
                "pair": pair,
                "direction": direction_use,
                "score": target.get("score"),
                "reason": reason,
                "mark_window": mark_window,
                "ts": time.time(),
                "ambiguous": True,
            }
            _save_state(st)
            _log(
                {
                    "event": "submit_ambiguous",
                    "pair": pair,
                    "direction": direction_use,
                    "error": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc()[-800:],
                }
            )
            try:
                ch.close()
            except Exception:
                pass
            _sleep(max(poll_s, 3600.0), "submit_ambiguous_hold_gap")
            continue
        try:
            ch.close()
        except Exception:
            pass

        if res.get("ok"):
            record_local_submit(hk)
            st["filled_windows"] = sorted(set(filled) | {int(mark_window)})
            st["last_submit"] = {
                "pair": pair,
                "direction": direction_use,
                "score": target.get("score"),
                "reason": reason,
                "mark_window": mark_window,
                "ts": time.time(),
            }
            _save_state(st)
            _log(
                {
                    "event": "submit_ok",
                    "reason": reason,
                    "window": window,
                    "mark_window": mark_window,
                    "pair": pair,
                    "direction": direction_use,
                    "score": target.get("score"),
                    **res,
                }
            )
            # short sleep then resume scanning (gap guard blocks until 1h)
            _sleep(poll_s, "post_submit_scan")
        else:
            # Same ambiguity class as exceptions — keep claim + enforce gap.
            try:
                record_local_submit(hk)
            except Exception:  # noqa: BLE001
                pass
            st["last_submit"] = {
                "pair": pair,
                "direction": direction_use,
                "score": target.get("score"),
                "reason": reason,
                "mark_window": mark_window,
                "ts": time.time(),
                "ambiguous": True,
            }
            _save_state(st)
            _log(
                {
                    "event": "submit_ambiguous",
                    "pair": pair,
                    "direction": direction_use,
                    "result": res,
                }
            )
            _sleep(max(poll_s, 3600.0), "submit_ambiguous_hold_gap")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="SN89 LF auto-submit: 3 best chances / UTC day (windowed)"
    )
    p.add_argument("--wallet.name", dest="wallet_name", default="rabi")
    p.add_argument("--wallet.hotkey", dest="wallet_hotkey", default="rabi-h1")
    p.add_argument(
        "--direction",
        default=os.getenv("SN89_AUTO_DIRECTION", "AUTO"),
        help="LONG, SHORT, or AUTO (pick best side by edge)",
    )
    p.add_argument(
        "--pairs",
        default=os.getenv("SN89_AUTO_PAIRS", ",".join(DEFAULT_PAIRS)),
    )
    p.add_argument(
        "--poll-s",
        type=float,
        default=float(os.getenv("SN89_AUTO_POLL_S", "180")),
        help="Seconds between score scans (default 180)",
    )
    p.add_argument(
        "--early-score",
        type=float,
        default=float(os.getenv("SN89_AUTO_EARLY_SCORE", "0.55")),
        help="Early-submit if decisive-adjusted score ≥ this",
    )
    p.add_argument(
        "--finalize-min",
        type=float,
        default=float(os.getenv("SN89_AUTO_FINALIZE_MIN", "90")),
        help="Minutes before window end to lock in the best so far",
    )
    p.add_argument(
        "--min-vol-band-early",
        type=float,
        default=float(os.getenv("SN89_AUTO_MIN_VOL_BAND_EARLY", "1.35")),
        help="Min 8h-vol/band for early submit (higher → fewer washes)",
    )
    p.add_argument(
        "--min-vol-band-submit",
        type=float,
        default=float(os.getenv("SN89_AUTO_MIN_VOL_BAND_SUBMIT", "1.2")),
        help="Min 8h-vol/band for any submit; skip slot if below",
    )
    p.add_argument(
        "--early-min-elapsed-min",
        type=float,
        default=float(os.getenv("SN89_AUTO_EARLY_MIN_ELAPSED_MIN", "180")),
        help="Minutes into an 8h window before early-submit is allowed",
    )
    p.add_argument(
        "--pick-rank",
        type=int,
        default=int(os.getenv("SN89_AUTO_PICK_RANK", "0")),
        help="0=best free signal, 1=2nd best, … (diversify multi-hotkey farms)",
    )
    args = p.parse_args(argv)
    pairs = [x.strip().upper() for x in args.pairs.split(",") if x.strip()]
    direction = args.direction.upper()
    if direction not in ("LONG", "SHORT", "AUTO"):
        print("direction must be LONG, SHORT, or AUTO", file=sys.stderr)
        return 2
    run(
        wallet_name=args.wallet_name,
        wallet_hotkey=args.wallet_hotkey,
        direction=direction,
        pairs=pairs,
        poll_s=args.poll_s,
        early_score=args.early_score,
        finalize_min=args.finalize_min,
        min_vol_band_early=args.min_vol_band_early,
        min_vol_band_submit=args.min_vol_band_submit,
        early_min_elapsed_min=args.early_min_elapsed_min,
        pick_rank=max(0, int(args.pick_rank)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
