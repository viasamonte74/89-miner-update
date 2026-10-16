# miner_ext — SN89 LF research + paper miner

Honest, grader-faithful tooling for InfiniteQuant Subnet (netuid 89).
See [PHASE0.md](PHASE0.md) for the plan, gates, and code-resolved rules.

**Defaults:** paper mode; no on-chain actions. Live submit needs an explicit flag
and operator confirmation.

## Layout

```
miner_ext/
  data/       tick archiver, parquet store, coverage
  backtest/   grader wrapper, LF rules, walk-forward, report
  train/      dataset / fit (Phase 3+)
  signals/    features / selector (Phase 3+)
  run/        paper / live / killswitch
  ops/        supervisord, backup, healthcheck
  tests/      parity + leakage guards
  results/    reports (gitignored content OK)
```

## Quick start

```bash
source /venv/main/bin/activate
cd /workspace/InfiniteQuant-Subnet
export PYTHONPATH=/workspace/InfiniteQuant-Subnet
export SN89_DATA_ROOT=/workspace/sn89_data

# Phase 0
python -m miner_ext.scripts.wilson_table

# Phase 1 — archive + tests
python -m miner_ext.data.tick_archiver --workers 2
python -m miner_ext.data.coverage
pytest miner_ext/tests -q

# Phase 2 — baselines (walk-forward vs grader)
python -m miner_ext.backtest.bars
# NOTE: miner_ext.backtest.run_baselines and miner_ext.train.retrain / features /
# build_dataset / signals.selector / protocol_adapter are currently missing as
# .py sources in this fork (only stale __pycache__ remains). Restore from
# DeltaCompute24/InfiniteQuant-Subnet or the operator box before running them.
python -m miner_ext.backtest.walkforward  # available modules only

# Grow archive continuously
python -m miner_ext.data.tick_archiver --loop --interval-s 180 --workers 2
```

Data root: `$SN89_DATA_ROOT` (default `/workspace/sn89_data/`).
