# Regime switcher (no ML) vs always_LONG

**Recommendation: `STOP_do_not_register_no_reliable_edge`**

Bull = BTC 8.0d log-ret ≥ 0 → always_LONG; bear → rule strategy.
Gate: beat always_LONG Wilson LB on ≥2/3 folds, perm p<0.05, ≥8 decisive.

## regime_momentum (beat AL on 0/3; pass=False)

| Fold | Hit | LB | Decis | Short | Bull/Bear | AL hit | AL LB | p_perm | Beat? |
|------|-----|----|-------|-------|-----------|--------|-------|--------|-------|
| fold1 | 0.389 | 0.257 | 18 | 1 | 11/2 | 0.389 | 0.257 | 0.415 | N |
| fold2 | 0.500 | 0.368 | 22 | 11 | 6/7 | 0.545 | 0.411 | 0.740 | N |
| fold3 | 0.406 | 0.302 | 32 | 3 | 10/3 | 0.448 | 0.336 | 0.710 | N |

Holdout: hit=0.467 LB=0.313 short=5 | AL hit=0.421 LB=0.288

## regime_first_passage (beat AL on 0/3; pass=False)

| Fold | Hit | LB | Decis | Short | Bull/Bear | AL hit | AL LB | p_perm | Beat? |
|------|-----|----|-------|-------|-----------|--------|-------|--------|-------|
| fold1 | 0.368 | 0.242 | 19 | 3 | 11/2 | 0.389 | 0.257 | 0.515 | N |
| fold2 | 0.273 | 0.170 | 22 | 12 | 6/7 | 0.545 | 0.411 | 0.715 | N |
| fold3 | 0.357 | 0.252 | 28 | 3 | 10/3 | 0.448 | 0.336 | 0.905 | N |

Holdout: hit=0.400 LB=0.230 short=10 | AL hit=0.421 LB=0.288

## regime_momentum_short_only (beat AL on 0/3; pass=False)

| Fold | Hit | LB | Decis | Short | Bull/Bear | AL hit | AL LB | p_perm | Beat? |
|------|-----|----|-------|-------|-----------|--------|-------|--------|-------|
| fold1 | 0.316 | 0.199 | 19 | 5 | 11/2 | 0.389 | 0.257 | 0.670 | N |
| fold2 | 0.391 | 0.272 | 23 | 19 | 6/7 | 0.545 | 0.411 | 0.440 | N |
| fold3 | 0.419 | 0.313 | 31 | 9 | 10/3 | 0.448 | 0.336 | 0.335 | N |

Holdout: hit=0.250 LB=0.139 short=10 | AL hit=0.421 LB=0.288

## Honesty notes

- No ML; regime from causal BTC lookback only.
- Flat/unknown BTC ret treated as bull (defaults to always_LONG).
- Beating always_LONG requires bear-window skill from the rule leg.
