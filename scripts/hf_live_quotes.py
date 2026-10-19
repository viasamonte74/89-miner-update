"""Fetch live SN89 quote ticks (same partner stream validators grade on)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

PARTNER_API = os.environ.get("SN89_PARTNER_API", "https://partner.infinitequant.app")
LIVE_ENABLED = os.environ.get("SN89_HF_LIVE_QUOTES", "1").strip() not in (
    "0",
    "false",
    "False",
)
# Shared-file TTL: processes poll ~30–45s, so 8s almost never shared; 30s is
# still well under the 120s freshness gate.
CACHE_TTL_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_TTL_S", "30"))
STREAM_WAIT_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_WAIT_S", "4"))
# Keep enough live ticks to cover fade windows even when 1m bars lag ~10–15m.
HIST_KEEP_MS = int(float(os.environ.get("SN89_HF_LIVE_HIST_MIN", "20")) * 60_000)
# Quotes older than this are treated as missing (fail closed).
MAX_QUOTE_AGE_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_MAX_AGE_S", "120"))
# Reject clock-skew / bad units that land more than this in the future.
MAX_QUOTE_FUTURE_S = float(os.environ.get("SN89_HF_LIVE_QUOTE_MAX_FUTURE_S", "60"))
DATA_ROOT = Path(os.environ.get("SN89_DATA_ROOT", "/workspace/sn89_data"))
SHARED_CACHE_PATH = Path(
    os.environ.get(
        "SN89_HF_LIVE_QUOTE_CACHE",
        str(DATA_ROOT / "hf_live_quotes_cache.json"),
    )
)

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


def _parse_tick_ms(d: dict) -> int | None:
    """Explicit timestamp fields only — never invent wall-clock freshness.

    Units: seconds (<1e12) → ×1000; microseconds/ns (≥1e14) → ÷1000 until ms.
    A bare µs value left as "ms" looks far in the future and would otherwise
    clamp to age 0 and pass the freshness gate.
    """
    raw = None
    for key in ("t", "ts_ms", "t_ms", "timestamp", "ts", "time"):
        if d.get(key) is not None:
            raw = d.get(key)
            break
    if raw is None:
        return None
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    # µs / ns (and larger) → milliseconds
    while t >= 1e14:
        t /= 1000.0
    # seconds → milliseconds
    if t < 1e12:
        t *= 1000.0
    return int(t)


def _record_hist(pair: str, t_ms: int, price: float) -> None:
    pair = pair.upper()
    buf = _hist.setdefault(pair, [])
    if buf and t_ms < buf[-1][0]:
        buf.append((t_ms, price))
        buf.sort(key=lambda x: x[0])
    elif buf and t_ms == buf[-1][0]:
        buf[-1] = (t_ms, price)
    else:
        buf.append((t_ms, price))
    cutoff = t_ms - HIST_KEEP_MS
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


def quote_age_s(quote: dict, *, now: float | None = None) -> float:
    """Signed age in seconds (negative = timestamp in the future)."""
    now_ms = int((now if now is not None else time.time()) * 1000)
    return (now_ms - int(quote["t_ms"])) / 1000.0


def quote_is_fresh(quote: dict | None, *, now: float | None = None) -> bool:
    if not quote:
        return False
    age = quote_age_s(quote, now=now)
    if age < -MAX_QUOTE_FUTURE_S:
        return False
    if age < 0:
        age = 0.0  # small clock skew OK
    return age <= MAX_QUOTE_AGE_S


def _read_shared_cache(pair: str, now: float) -> dict | None:
    try:
        if not SHARED_CACHE_PATH.exists():
            return None
        data = json.loads(SHARED_CACHE_PATH.read_text(encoding="utf-8"))
        row = (data.get("quotes") or {}).get(pair.upper())
        if not row:
            return None
        fetched_at = float(row.get("fetched_at") or 0)
        if now - fetched_at > CACHE_TTL_S:
            return None
        t_ms = int(row["t_ms"])
        price = float(row["price"])
        q = {
            "pair": pair.upper(),
            "price": price,
            "t_ms": t_ms,
            "cached": True,
            "shared": True,
        }
        if not quote_is_fresh(q, now=now):
            return None
        _cache[pair.upper()] = (fetched_at, price, float(t_ms))
        _record_hist(pair.upper(), t_ms, price)
        return q
    except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return None


def _write_shared_cache(pair: str, price: float, t_ms: int, fetched_at: float) -> None:
    import fcntl

    try:
        SHARED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock = SHARED_CACHE_PATH.with_suffix(".lock")
        lock.touch(exist_ok=True)
        with lock.open("a+", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                data: dict = {"quotes": {}}
                if SHARED_CACHE_PATH.exists():
                    try:
                        data = json.loads(SHARED_CACHE_PATH.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        data = {"quotes": {}}
                quotes = data.setdefault("quotes", {})
                quotes[pair.upper()] = {
                    "price": float(price),
                    "t_ms": int(t_ms),
                    "fetched_at": float(fetched_at),
                }
                # Drop very old entries.
                cutoff = fetched_at - 600
                for k in list(quotes.keys()):
                    if float(quotes[k].get("fetched_at") or 0) < cutoff:
                        del quotes[k]
                tmp = SHARED_CACHE_PATH.with_suffix(".tmp")
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.replace(tmp, SHARED_CACHE_PATH)
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def fetch_live_price(pair: str, wallet: Any | None = None, token: str | None = None) -> dict | None:
    """Return {pair, price, t_ms} from the partner SSE stream, or None.

    Fail closed: missing/unparseable timestamp → no quote (never invent now()).
    """
    if not LIVE_ENABLED:
        return None
    pair = pair.upper()
    now = time.time()
    cached = _cache.get(pair)
    if cached and now - cached[0] < CACHE_TTL_S:
        q = {"pair": pair, "price": cached[1], "t_ms": int(cached[2]), "cached": True}
        if quote_is_fresh(q, now=now):
            return q
    shared = _read_shared_cache(pair, now)
    if shared is not None:
        return shared

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
                t_ms = _parse_tick_ms(d)
                if t_ms is None:
                    # Stale-looking payload without a real clock — skip.
                    continue
                try:
                    price = float(px)
                except (TypeError, ValueError):
                    continue
                q = {
                    "pair": pair,
                    "price": price,
                    "t_ms": t_ms,
                    "cached": False,
                    "shared": False,
                }
                if not quote_is_fresh(q):
                    continue
                fetched_at = time.time()
                _cache[pair] = (fetched_at, price, float(t_ms))
                _record_hist(pair, t_ms, price)
                _write_shared_cache(pair, price, t_ms, fetched_at)
                return q
    except Exception:  # noqa: BLE001
        return None
    return None


def append_live_to_series(
    t_ms: Any, close: Any, quote: dict
) -> tuple[Any, Any]:
    """Append live tip only — do NOT bridge archive gaps with the last close.

    Forward-filling the old close across a 10–30m gap invents a flat tape then
    a fake spike when the live tip lands (majority of random moments look like
    ≥1.75σ). Prefer fail-closed / live-hist overlay instead.
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
