# Phase 3 — Model tournament vs always_LONG

**Recommendation: `STOP_do_not_register_no_reliable_edge`**

Selection policy: `al_prior_override` (al_prior_override = always_LONG prior + high-p model overrides).

Gate: beat always_LONG Wilson LB on ≥2/3 folds with permutation p<0.05 and ≥8 decisive in the fold schedule.

## xgboost (beat AL on 0/3 folds; pass=False)

| Fold | Thr | Hit | LB | Decis | Short | AL hit | AL LB | AUC | p_perm | Beat? |
|------|-----|-----|----|-------|-------|--------|-------|-----|--------|-------|
| fold2 | 0.90 | 0.600 | 0.472 | 25 | 0 | 0.600 | 0.472 | 0.490 | 1.000 | N |
| fold3 | 0.90 | 0.591 | 0.455 | 22 | 0 | 0.591 | 0.455 | 0.500 | 1.000 | N |

Holdout: hit=0.625 LB=0.465 dec=16 short=0 | AL hit=0.625 LB=0.465

## Honesty notes

- ~45 independent days; fold variance is high.
- always_LONG remains the regime-biased incumbent from Phase 2.
- `al_prior_override` only beats AL if overrides add skill; high thr ≈ AL.
- Neural challengers (TCN/GRU) deferred: tabular must clear the gate first.
- If recommendation is STOP: valid successful outcome under the plan.
