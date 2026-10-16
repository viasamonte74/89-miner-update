# Rule-change gate (SN89 rabi fleet)

Any change to direction rules, priors, or HF volume must ship with:

1. A walk-forward report under `miner_ext/results/` (see `scripts/backtest/hf_fade_walkforward.py`).
2. Both halves of the sample agreeing within ~3pp hit rate (no regime flip).
3. Tick grader-parity validation before trusting production HR (1m bars only approximate).

Current production HF direction: `SN89_HF_DIR_MODE=fade_spike` (env-gated).
Fleet priors: `SN89_FLEET_PRIORS_MODE=off` (re-enable only as `wilson` with n≥30).
LF: `SN89_LF_QUALITY_ONLY=1` — no finalize/eod/catch-up forced fills.
