"""Fetch live SN89 quote ticks (same partner stream validators grade on)."""
from __future__ import annotations

import json
import os
import time
from typing import Any

PARTNER_API = os.environ.get("SN89_PARTNER_API", "https://partner.infinitequant.app")
LIVE_ENABLED = os.environ.get("SN89_HF_LIVE_QUOTES", "1").strip() not in (
    "0",
    "false",
    "False",
)
CACHE_TTL_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_TTL_S", "8"))
STREAM_WAIT_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_WAIT_S", "4"))

_cache: dict[str, tuple[float, float, float]] = {}  # pair -> (fetched_at, price, tick_ms)
_token_cache: dict[str, tuple[float, str]] = {}  # hotkey -> (ts, token)


def _mint_token(wallet: Any) -> str:
    from neurons.miner import mint_miner_token

    hk = wallet.hotkey.ss58_address
    hit = _token_cache.get(hk)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    tok = mint_miner_token(wallet)
    _token_cache[hk] = (time.time(), tok)
    return tok


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
                return {"pair": pair, "price": price, "t_ms": t_ms, "cached": False}
    except Exception:  # noqa: BLE001
        return None
    return None


def append_live_to_series(
    t_ms: Any, close: Any, quote: dict
) -> tuple[Any, Any]:
    """Append live tick to numpy arrays used by score_hf."""
    import numpy as np

    t = np.asarray(t_ms, dtype=np.int64)
    c = np.asarray(close, dtype=np.float64)
    qt = int(quote["t_ms"])
    qp = float(quote["price"])
    if len(t) and qt <= int(t[-1]):
        # Replace last if same/older ms — keep series monotonic.
        c = c.copy()
        c[-1] = qp
        return t, c
    return np.append(t, qt), np.append(c, qp)
