"""Fetch live SN89 quote ticks (same partner stream validators grade on)."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import numpy as np

PARTNER_API = os.environ.get("SN89_PARTNER_API", "https://partner.infinitequant.app")
LIVE_ENABLED = os.environ.get("SN89_HF_LIVE_QUOTES", "1").strip() not in (
    "0",
    "false",
    "False",
)
CACHE_TTL_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_TTL_S", "8"))
STREAM_WAIT_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_WAIT_S", "4"))
# Keep enough live ticks to cover fade windows even when 1m bars lag ~10–15m.
HIST_KEEP_MS = int(float(os.environ.get("SN89_HF_LIVE_HIST_MIN", "20")) * 60_000)

_cache: dict[str, tuple[float, float, float]] = {}  # pair -> (fetched_at, price, tick_ms)
_token_cache: dict[str, tuple[float, str]] = {}  # hotkey -> (ts, token)
_hist: dict[str, list[tuple[int, float]]] = {}  # pair -> [(t_ms, price), ...] ascending


def _mint_token(wallet: Any) -> str:
    from neurons.miner import mint_miner_token

    hk = wallet.hotkey.ss58_address
    hit = _token_cache.get(hk)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    tok = mint_miner_token(wallet)
    _token_cache[hk] = (time.time(), tok)
    return tok


def _record_hist(pair: str, t_ms: int, price: float) -> None:
    pair = pair.upper()
    buf = _hist.setdefault(pair, [])
    if buf and t_ms < buf[-1][0]:
        # Out-of-order: insert sorted, rare.
        buf.append((t_ms, price))
        buf.sort(key=lambda x: x[0])
    elif buf and t_ms == buf[-1][0]:
        buf[-1] = (t_ms, price)
    else:
        buf.append((t_ms, price))
    cutoff = t_ms - HIST_KEEP_MS
    # Drop from left.
    i = 0
    while i < len(buf) and buf[i][0] < cutoff:
        i += 1
    if i:
        del buf[:i]


def live_hist(pair: str) -> list[tuple[int, float]]:
    return list(_hist.get(pair.upper(), []))


def hist_price_at_or_before(pair: str, t_ms: int) -> float | None:
    buf = _hist.get(pair.upper()) or []
    if not buf:
        return None
    best = None
    for ts, px in buf:
        if ts <= t_ms:
            best = px
        else:
            break
    return best


def fetch_live_price(pair: str, wallet: Any | None = None, token: str | None = None) -> dict | None:
    """Return {pair, price, t_ms} from the partner SSE stream, or None."""
    if not LIVE_ENABLED:
        return None
    pair = pair.upper()
    now = time.time()
    cached = _cache.get(pair)
    if cached and now - cached[0] < CACHE_TTL_S:
        return {"pair": pair, "price": cached[1], "t_ms": int(cached[2]), "cached": True}

    tok = token
    if not tok:
        if wallet is None:
            return None
        try:
            tok = _mint_token(wallet)
        except Exception:  # noqa: BLE001
            return None

    import requests

    url = f"{PARTNER_API}/api/sn89/closers/stream?pair={pair}"
    deadline = time.time() + STREAM_WAIT_S
    try:
        with requests.get(
            url,
            headers={"Authorization": f"Bearer {tok}"},
            stream=True,
            timeout=(5, STREAM_WAIT_S + 2),
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if time.time() > deadline:
                    break
                if not line or not line.startswith(b"data:"):
                    continue
                try:
                    d = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                px = d.get("price")
                if px is None:
                    continue
                t_ms = d.get("t") or d.get("ts_ms") or d.get("t_ms") or int(time.time() * 1000)
                try:
                    price = float(px)
                    t_ms = int(t_ms)
                except (TypeError, ValueError):
                    continue
                _cache[pair] = (time.time(), price, float(t_ms))
                _record_hist(pair, t_ms, price)
                return {"pair": pair, "price": price, "t_ms": t_ms, "cached": False}
    except Exception:  # noqa: BLE001
        return None
    return None


def append_live_to_series(
    t_ms: Any, close: Any, quote: dict
) -> tuple[Any, Any]:
    """Append live tick; bridge large gaps with 1m forward-fills of last close.

    Bridging keeps index-based lookbacks usable when the archive lags ~10–15m.
    The final point is the live price so decision_ms is current.
    """
    t = np.asarray(t_ms, dtype=np.int64)
    c = np.asarray(close, dtype=np.float64)
    qt = int(quote["t_ms"])
    qp = float(quote["price"])
    if len(t) == 0:
        return np.array([qt], dtype=np.int64), np.array([qp], dtype=np.float64)
    if qt <= int(t[-1]):
        c = c.copy()
        c[-1] = qp
        return t, c
    last_t = int(t[-1])
    last_c = float(c[-1])
    gap_ms = qt - last_t
    # Bridge minute marks between last bar and live (exclusive of live).
    if gap_ms > 90_000:
        step = 60_000
        fill_t = list(range(last_t + step, qt, step))
        if fill_t:
            t = np.concatenate([t, np.asarray(fill_t, dtype=np.int64)])
            c = np.concatenate([c, np.full(len(fill_t), last_c, dtype=np.float64)])
    return np.append(t, qt), np.append(c, qp)


def merge_hist_into_series(
    t_ms: Any, close: Any, pair: str, *, decision_ms: int | None = None
) -> tuple[Any, Any]:
    """Overlay live hist ticks onto bar series (monotonic by time)."""
    t = np.asarray(t_ms, dtype=np.int64)
    c = np.asarray(close, dtype=np.float64)
    hist = live_hist(pair)
    if not hist:
        return t, c
    decision_ms = int(decision_ms or hist[-1][0])
    for ht, hp in hist:
        if ht > decision_ms:
            break
        if len(t) == 0 or ht > int(t[-1]):
            t = np.append(t, ht)
            c = np.append(c, hp)
        elif ht == int(t[-1]):
            c = c.copy()
            c[-1] = hp
    return t, c
