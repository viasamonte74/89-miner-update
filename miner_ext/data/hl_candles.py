"""Hyperliquid candleSnapshot helper (supplement only — never mix silently).

Label every series with source='hyperliquid'. Validate transfer vs anchored ticks
before trusting OOS claims built on HL candles.
"""
from __future__ import annotations

import json
import urllib.request

from .paths import USER_AGENT

HL_INFO = "https://api.hyperliquid.xyz/info"


def candle_snapshot(coin: str, interval: str, start_ms: int, end_ms: int) -> list:
    body = json.dumps(
        {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        }
    ).encode()
    req = urllib.request.Request(
        HL_INFO,
        data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())
