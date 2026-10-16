"""Phase 2 baseline signal generators (features causal w.r.t. decision time)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from miner_ext.backtest.bars import load_close_series
from miner_ext.backtest.rules import ScheduledCall


@dataclass
class Candidate:
    pair: str
    direction: str
    t0_unix: float
    score: float  # higher = more conviction
    reason: str


def _idx_at_or_before(t_ms: np.ndarray, decision_ms: int) -> int:
    """Largest index with t_ms[i] <= decision_ms; -1 if none."""
    i = int(np.searchsorted(t_ms, decision_ms, side="right") - 1)
    return i


def realized_stats(
    t_ms: np.ndarray, close: np.ndarray, decision_ms: int, lookback_min: int = 1440
) -> tuple[float, float, float] | None:
    """Return (last_close, drift_per_min, vol_per_sqrt_min) from 1m log returns.

    Uses bars strictly at or before decision_ms (causal).
    """
    i = _idx_at_or_before(t_ms, decision_ms)
    if i < lookback_min + 1:
        return None
    sl = close[i - lookback_min : i + 1]
    if np.any(~np.isfinite(sl)) or sl[-1] <= 0:
        return None
    rets = np.diff(np.log(sl))
    mu = float(np.mean(rets))
    sig = float(np.std(rets, ddof=1))
    if not np.isfinite(sig) or sig <= 1e-12:
        return None
    return float(sl[-1]), mu, sig


def momentum_score(
    t_ms: np.ndarray, close: np.ndarray, decision_ms: int, lag_min: int = 60
) -> float | None:
    i = _idx_at_or_before(t_ms, decision_ms)
    if i < lag_min:
        return None
    c0, c1 = float(close[i - lag_min]), float(close[i])
    if c0 <= 0 or not np.isfinite(c0) or not np.isfinite(c1):
        return None
    return math.log(c1 / c0)


def mean_reversion_score(
    t_ms: np.ndarray, close: np.ndarray, decision_ms: int, window_min: int = 240
) -> float | None:
    """Negative z-score of last close vs trailing window → fade extreme moves."""
    i = _idx_at_or_before(t_ms, decision_ms)
    if i < window_min:
        return None
    w = close[i - window_min + 1 : i + 1]
    mu = float(np.mean(w))
    sd = float(np.std(w, ddof=1))
    if sd <= 1e-12 or not np.isfinite(sd):
        return None
    return -(float(w[-1]) - mu) / sd  # positive ⇒ expect bounce up ⇒ LONG


def first_passage_p_long(
    mu_per_min: float, sig_per_sqrt_min: float, band_bps: float
) -> float:
    """P(hit +a before -a) for BM; a = band as fraction of price.

    γ = 2 μ a / σ²  (rates per minute). P = 1/(1+e^{-γ}).
    """
    a = band_bps / 10_000.0
    sig2 = sig_per_sqrt_min * sig_per_sqrt_min
    if sig2 <= 0:
        return 0.5
    gamma = 2.0 * mu_per_min * a / sig2
    # numerically stable
    if gamma > 30:
        return 1.0
    if gamma < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-gamma))


# ── strategy callables: (pair, decision_unix, series_cache, band_bps) -> Candidate|None


SeriesCache = dict[str, tuple[np.ndarray, np.ndarray]]


def always_long(pair: str, t0: float, cache: SeriesCache, band_bps: float) -> Candidate | None:
    # Equal edge; slight deterministic jitter so top-k diversifies across pairs/days.
    jitter = ((hash((pair, int(t0) // 86400)) & 0xFFFF) / 1e6)
    return Candidate(pair, "LONG", t0, 1.0 + jitter, "always_long")


def random_dir(
    pair: str, t0: float, cache: SeriesCache, band_bps: float, seed: int = 89
) -> Candidate | None:
    # deterministic per (pair, minute)
    h = hash((pair, int(t0) // 60, seed)) & 0xFFFFFFFF
    direction = "LONG" if (h % 2 == 0) else "SHORT"
    score = 0.5 + ((h >> 1) & 0xFFFF) / 1e6
    return Candidate(pair, direction, t0, score, "random")


def momentum(
    pair: str, t0: float, cache: SeriesCache, band_bps: float, lag_min: int = 60
) -> Candidate | None:
    t_ms, close = cache[pair]
    s = momentum_score(t_ms, close, int(t0 * 1000), lag_min=lag_min)
    if s is None or abs(s) < 1e-6:
        return None
    direction = "LONG" if s > 0 else "SHORT"
    return Candidate(pair, direction, t0, abs(s), f"mom_{lag_min}m")


def mean_reversion(
    pair: str, t0: float, cache: SeriesCache, band_bps: float, window_min: int = 240
) -> Candidate | None:
    t_ms, close = cache[pair]
    z = mean_reversion_score(t_ms, close, int(t0 * 1000), window_min=window_min)
    if z is None or abs(z) < 0.5:  # weak signal skip
        return None
    direction = "LONG" if z > 0 else "SHORT"
    return Candidate(pair, direction, t0, abs(z), f"mr_z{window_min}")


def first_passage(
    pair: str, t0: float, cache: SeriesCache, band_bps: float, lookback_min: int = 1440
) -> Candidate | None:
    t_ms, close = cache[pair]
    stats = realized_stats(t_ms, close, int(t0 * 1000), lookback_min=lookback_min)
    if stats is None:
        return None
    _, mu, sig = stats
    p_long = first_passage_p_long(mu, sig, band_bps)
    edge = abs(p_long - 0.5)
    if edge < 0.02:  # near coin-flip → skip
        return None
    direction = "LONG" if p_long >= 0.5 else "SHORT"
    score = p_long if direction == "LONG" else (1.0 - p_long)
    return Candidate(pair, direction, t0, score, f"fp_p={p_long:.3f}")


STRATEGIES: dict[str, Callable[..., Candidate | None]] = {
    "always_long": always_long,
    "random": random_dir,
    "momentum": momentum,
    "mean_reversion": mean_reversion,
    "first_passage": first_passage,
}


def load_series_cache(pairs: list[str]) -> SeriesCache:
    cache: SeriesCache = {}
    for pair in pairs:
        cache[pair] = load_close_series(pair)
    return cache


def select_daily_top_k(
    candidates: list[Candidate],
    *,
    k: int = 3,
    min_gap_s: float = 3600.0,
) -> list[ScheduledCall]:
    """Per UTC-day greedy: highest score first, cap k, min gap, soft pair diversity."""
    by_day: dict[int, list[Candidate]] = {}
    for c in candidates:
        by_day.setdefault(int(c.t0_unix // 86_400), []).append(c)

    chosen: list[ScheduledCall] = []
    for day in sorted(by_day):
        remaining = list(by_day[day])
        day_times: list[float] = []
        used_pairs: set[str] = set()
        while len(day_times) < k and remaining:
            remaining.sort(
                key=lambda c: (
                    -(c.score - (0.05 if c.pair in used_pairs else 0.0)),
                    c.t0_unix,
                    c.pair,
                )
            )
            placed = False
            for i, c in enumerate(remaining):
                if day_times and (c.t0_unix - max(day_times)) < min_gap_s:
                    continue
                chosen.append(ScheduledCall(c.pair, c.direction, c.t0_unix))
                day_times.append(c.t0_unix)
                used_pairs.add(c.pair)
                remaining.pop(i)
                placed = True
                break
            if not placed:
                break
    return sorted(chosen, key=lambda x: x.t0_unix)
