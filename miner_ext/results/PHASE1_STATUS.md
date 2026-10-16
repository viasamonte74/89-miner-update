# Phase 1 status (2026-10-09)

## Done
- Phase 0 plan written: `miner_ext/PHASE0.md` (code-resolved QUALIFY window, Wilson table, grading, submit).
- Scaffold: data / backtest / ops / run / tests.
- Grader-parity tests: **10 passed** (`pytest miner_ext/tests`).
- Tick archiver started under `/workspace/sn89_data/raw_windows` (resume-safe, 3 workers).

## In progress
- Full ~45d tick archive (~8–10 GB, multi-hour). Monitor: `tail -f /workspace/logs/tick_archiver.log`
- Health: `python -m miner_ext.ops.healthcheck`

## Next
- When archive has meaningful coverage: live-archive parity smoke (grade_call with use_archive=True).
- Phase 2 baselines on walk-forward folds.

## Gates
- Phase 1 gate: backtester matches grader on tests — **PASS** (synthetic injected ticks).
- Live-feed parity vs archived windows — pending data pull.
