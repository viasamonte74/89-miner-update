# Phase 0 — Plan (SN89 LF crypto miner)

**Status:** approved to execute (2026-10-09). Scope: LF mechanism 0, crypto pairs first.
**Honest prior:** short-horizon crypto direction is hard; “no edge → do not register” is a valid success.

## Objective

Determine with grader-faithful evidence whether a model can reach **LF QUALIFIED**
(≥ 8 decisive in the *reputation* window AND Wilson LB ≥ 50%), and build the miner
only as far as that evidence justifies.

## Resolve-from-code (citations)

### 1. QUALIFIED window and shrunk tiers

- **Qualify / tier sample** = reputation window: trailing `HIT_RATE_WINDOW_S` = **60 days**,
  capped at `hit_rate_window_trades_as_of(now)` = **200** decisive since
  `HIT_RATE_WINDOW_TRADES_V2_FROM` (2026-07-25). See `config.py` ~438–541,
  `scoring.score_inputs` ~878–920, `scoring.is_qualified` ~1173–1177.
- **Not** the 7-day window. `EMISSION_DECAY_S` = 7 days is how banked qualified wins
  decay for *emission weight* (`config.py` ~231–236).
- **Shrunk hit-rate** for tiers: `(wins + K/2) / (n + K)` with `TIER_PRIOR_K=12`
  (`scoring.shrunk_hit_rate` ~138–141). Anchors: ≥55% → 1.0×, ≥60% → 1.2×, ≥70% → 2.0×.
  Live wins also use the **continuous ramp** (`CONTINUOUS_TIER_FROM`, `_tier_ramp`).
- Extra live emission terms (must reuse, not reimplement): wash **efficiency** multiplier
  (`EFFICIENCY_*`), causal qwin (`CAUSAL_QWIN_FROM`), `WIN_CAP=20`, 8-day immunity.

### 2. LF board pairs

- Source of truth: `data/signals-bands.json` version `hluniverse-20261007` → **50 pairs**
  (note text saying “18” is stale). `config.bands_as_of` / history for as-of bands.
- Class counts: crypto 30, equities 11, indices 3, forex 3, metals 2, commodities 1.
- Phase 1–3 train on the **30 crypto** pairs only.

### 3. Live grading rule

- `config.grading_rule_as_of` → `touch_ticks` for `t0 >= TOUCH_TICKS_FROM` (2026-07-25).
- Entry = last tick mid `p` at/before t0; level needs `MIN_TOUCH_TICKS=2` touches
  (`grader._grade_touch_ticks` ~82–160). SL checked before TP in the walk; `touch_hit`
  notes TP/SL sit on opposite sides so one mid cannot hit both.
- Abandon: `hf_grade.GRADE_ABANDON_S` = **21600 s (6 h)** past horizon; missing entry →
  `WASHED` / `no_entry_price` past abandon (injected ticks skip abandon path).
- README “1-second bar” / “1-minute candle” wording is outdated for live grading.

### 4. Miner submit path

- `neurons/miner.py`: `submit(wallet, sig)` → `crypto.encrypt` (drand tlock + owner PK),
  blob upload, on-chain commitment. Modes: `submit` / `serve` / `follow`.
- Local limits: `check_local_limits` → `submission_rules_as_of` → **3/UTC day**, **≥1 h gap**
  (era-aware). Owner relay may need `SN89_FEED_TOKEN` (never log/print).
- Do **not** use `follow` for our live signals (copy risk).

### 5. Wilson target table (repo `confident_edge`)

| n | min wins | raw % | Wilson LB | shrunk | cont. tier |
|---|----------|-------|-----------|--------|------------|
| 8 | 6 | 75.0 | 0.524 | 0.600 | 1.20× |
| 9 | 7 | 77.8 | 0.566 | 0.619 | 1.35× |
| 10 | 8 | 80.0 | 0.602 | 0.636 | 1.49× |
| 12 | 9 | 75.0 | 0.567 | 0.625 | 1.40× |
| 15 | 10 | 66.7 | 0.501 | 0.593 | 1.17× |
| 20 | 13 | 65.0 | 0.507 | 0.594 | 1.18× |
| 21 | 14 | 66.7 | 0.527 | 0.606 | 1.25× |

At n=8, **6/8** clears QUALIFIED (not 7/8 or 8/8). Regenerate: `python -m miner_ext.scripts.wilson_table`.

## Environment (this Vast box)

| Item | Value |
|------|--------|
| GPU | NVIDIA GeForce RTX 3060 Ti, 8192 MiB, cc **8.6** (Ampere → bf16 OK) |
| CUDA driver | 595.x (CUDA 13.x capable); use PyTorch cu12.x wheels |
| CPU / RAM / disk | i5-class 6c, 32 GB RAM, **300 GB** overlay, **no volume** |
| Python | research: 3.12 `/venv/main`; miner: **3.10.21** via `uv` |
| Work root | `/workspace/InfiniteQuant-Subnet` (everything under `/workspace`) |

Network (probe 2026-10-09): tick index ~0.6 s OK; Hyperliquid POST OK; drand OK; BT entrypoint reachable.

**Rules:** GPU for training only; live inference on CPU; hotkey-only on this host; no mainnet/testnet submit without explicit OK; nightly off-box backup of ticks/models/results (never keys).

## Model tournament (trimmed)

1. First-passage baseline → 2. Logistic → 3. **XGBoost GPU incumbent** (+ LightGBM CPU check).
4. TCN/GRU **only if** XGB beats baselines OOS under multiple-testing correction and we still need more.
5. Selection layer: calibrated P(win) + threshold + top-k≤3/day; wash-rate filter.
6. Stop at Phase 3 if no model beats baselines on ≥2/3 folds after correction.

## Data / time estimate

| Item | Estimate |
|------|----------|
| Anchored ticks | ~45 d × ~0.4 MB/180 s window ≈ **8–10 GB** raw; parquet less |
| Full archive (polite, 2–4 workers) | **~2–6 h** first pull; then incremental |
| Phase 1 backtester + parity tests | **0.5–1 day** |
| Phase 2 baselines walk-forward | **0.5 day** once data landed |
| Phase 3 XGB search | **hours** on 3060 Ti (small tabular) |
| Independent decision days | **~45** — overfitting risk dominates compute |

## Phase gates

| Phase | Deliverable | Gate |
|-------|-------------|------|
| 0 | This plan | ✓ approved |
| 1 | Tick store + grader-parity backtester | Matches grader on tests |
| 2 | Baselines | Numbers reported |
| 3 | Models | Beat best baseline OOS w/ correction on ≥2/3 folds else **STOP** |
| 4 | ≥7 d paper | Consistent with backtest (ops evidence, weak stats) |
| 5 | Testnet 496 then mainnet | Explicit human OK only |

## Recommendation stance (pre-evidence)

Do not register until Phase 3–4 evidence exists. Default outcome: **no reliable edge**.
