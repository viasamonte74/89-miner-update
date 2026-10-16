"""Walk-forward fold helpers (Phase 2+). Embargo >= horizon."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fold:
    name: str
    train_start: float  # unix
    train_end: float
    test_start: float
    test_end: float


def make_folds(
    data_start: float,
    data_end: float,
    *,
    n_folds: int = 3,
    embargo_s: float = 8 * 3600,
    holdout_s: float = 8 * 86400,
) -> tuple[list[Fold], tuple[float, float]]:
    """Build n walk-forward folds; final holdout_s days untouched.

    Returns (folds, (holdout_start, holdout_end)).
    """
    if data_end - data_start <= holdout_s + embargo_s:
        raise ValueError("insufficient span for holdout + embargo")
    holdout_start = data_end - holdout_s
    usable_end = holdout_start - embargo_s
    span = usable_end - data_start
    if span <= 0 or n_folds < 1:
        raise ValueError("bad fold geometry")
    fold_span = span / n_folds
    folds: list[Fold] = []
    for i in range(n_folds):
        test_start = data_start + i * fold_span
        test_end = test_start + fold_span
        train_end = test_start - embargo_s
        train_start = data_start
        if train_end <= train_start:
            continue
        folds.append(
            Fold(
                name=f"fold{i+1}",
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=min(test_end, usable_end),
            )
        )
    return folds, (holdout_start, data_end)
