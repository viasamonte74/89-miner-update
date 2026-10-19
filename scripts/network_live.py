"""Network-wide live (pair, direction) from the published SN89 checkpoint.

Used to avoid landing second into another miner's open LF trade — the validator
marks those as is_copy and refuses to credit wins.

Cache is shared across fleet processes via a small JSON file so 8 loops do not
each pull the full checkpoint every poll.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

CHECKPOINT_URL = os.environ.get(
    "SN89_NETWORK_LIVE_URL",
    "https://partner.infinitequant.app/sn89/checkpoint.json",
)
CACHE_PATH = Path(
    os.environ.get(
        "SN89_NETWORK_LIVE_CACHE",
        "/workspace/sn89_data/network_live_cache.json",
    )
)
TTL_S = float(os.environ.get("SN89_NETWORK_LIVE_TTL_S", "90"))
# If refresh fails and cache is older than this, fail closed (block submits)
# instead of trading blind on empty/ancient holdings.
FAIL_CLOSED_S = float(os.environ.get("SN89_NETWORK_LIVE_FAIL_CLOSED_S", "900"))
DEFAULT_HORIZON_H = float(os.environ.get("SN89_NETWORK_LIVE_HORIZON_H", "8"))
_UA = {"User-Agent": "sn89-network-live/1"}


def _plaintext(s: dict) -> dict:
    p = s.get("plaintext")
    if isinstance(p, str):
        try:
            return json.loads(p) or {}
        except json.JSONDecodeError:
            return {}
    return p if isinstance(p, dict) else {}


def _fetch_pending() -> list[dict]:
    req = urllib.request.Request(CHECKPOINT_URL, headers=_UA)
    with urllib.request.urlopen(req, timeout=90) as resp:
        cp = json.loads(resp.read().decode("utf-8"))
    now = float(cp.get("now_unix") or time.time())
    out: list[dict] = []
    for s in cp.get("signals") or []:
        if s.get("status") != "pending":
            continue
        p = _plaintext(s)
        pair = str(p.get("trade_pair") or "").upper()
        direction = str(p.get("direction") or "").upper()
        if not pair or not direction:
            continue
        t0 = float(s.get("t0_unix") or 0.0)
        horizon_h = float(p.get("horizon_h") or DEFAULT_HORIZON_H)
        end = t0 + horizon_h * 3600.0
        exit_ms = s.get("exit_at_ms")
        if exit_ms is not None:
            try:
                end = min(end, float(exit_ms) / 1000.0)
            except (TypeError, ValueError):
                pass
        if end <= now:
            continue
        out.append(
            {
                "hotkey": s.get("hotkey") or "",
                "pair": pair,
                "direction": direction,
                "t0": t0,
                "end": end,
            }
        )
    return out


def refresh_cache(*, force: bool = False) -> dict:
    """Return cache dict; refresh from network if stale."""
    now = time.time()
    if not force and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if now - float(cached.get("fetched_at") or 0) < TTL_S:
                return cached
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = CACHE_PATH.with_suffix(".lock")
    import fcntl

    lock.touch(exist_ok=True)
    with lock.open("a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            # Re-check after lock (another process may have refreshed).
            if not force and CACHE_PATH.exists():
                try:
                    cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                    if now - float(cached.get("fetched_at") or 0) < TTL_S:
                        return cached
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass
            rows = _fetch_pending()
            payload = {
                "fetched_at": time.time(),
                "n": len(rows),
                "pending": rows,
            }
            tmp = CACHE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, CACHE_PATH)
            return payload
        except Exception as e:  # noqa: BLE001
            if CACHE_PATH.exists():
                try:
                    cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                    age = now - float(cached.get("fetched_at") or 0)
                    cached = dict(cached)
                    cached["error"] = str(e)
                    cached["stale"] = age > FAIL_CLOSED_S
                    cached["fail_closed"] = bool(force and age > FAIL_CLOSED_S)
                    return cached
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass
            return {
                "fetched_at": 0,
                "n": 0,
                "pending": [],
                "error": str(e),
                "stale": True,
                "fail_closed": bool(force),
            }
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def network_live_trades(
    now: float | None = None,
    *,
    exclude_hotkeys: set[str] | frozenset[str] | None = None,
    force: bool = False,
) -> set[tuple[str, str]]:
    """{(pair, direction)} open on-chain (pending) for other hotkeys.

    Raises ``RuntimeError`` when ``force`` and the cache is too stale to trust
    (caller should skip the submit rather than trade blind).
    """
    now = float(now if now is not None else time.time())
    exclude = {h for h in (exclude_hotkeys or set()) if h}
    cache = refresh_cache(force=force)
    if cache.get("fail_closed"):
        raise RuntimeError(
            f"network_live cache unusable: {cache.get('error') or 'stale'}"
        )
    live: set[tuple[str, str]] = set()
    for row in cache.get("pending") or []:
        hk = row.get("hotkey") or ""
        if hk and hk in exclude:
            continue
        if float(row.get("end") or 0) <= now:
            continue
        pair = str(row.get("pair") or "").upper()
        direction = str(row.get("direction") or "").upper()
        if pair and direction:
            live.add((pair, direction))
    return live


def network_live_pairs(
    now: float | None = None,
    *,
    exclude_hotkeys: set[str] | frozenset[str] | None = None,
    force: bool = False,
) -> set[str]:
    """Pairs with any open pending trade on a non-fleet hotkey."""
    return {
        pair
        for pair, _dir in network_live_trades(
            now, exclude_hotkeys=exclude_hotkeys, force=force
        )
    }
