# SN89 rabi fleet update (2026-10-16)

Fork: `viasamonte74/89-miner-update` @ `/workspace/InfiniteQuant-Subnet`.

## What changed

### P0 (agreed — shipped)
1. **HF diversity guard** (`scripts/fleet_hf_diversity.py` + `hf_auto_loop.py`)
   - Rebuilds trailing book from `~/.sn89/hf_receipts_<ss58>.jsonl` (+ `submit_ok` log).
   - Calls `sn89_signals.hf.hf_diversity` (no re-derive). Verified h1 receipts → share `0.15625`, floor ≈ `0.272`, matches board.
   - Before submit (once projected `n ≥ 30`): require `share ≥ floor + margin`.
   - Scores both sides; minority relief when edge ≥ `−MINORITY_EDGE_TOL`.
   - Prefer ≤30m horizons while below target; wait if nothing clears the guard.
2. **LF h2/h3 SHORT-only off** — fleet launcher sets `SN89_STRICT_MODE=0`, `SN89_STRICT_PREFER_SHORT=0`, `SN89_LF_QUALITY_FLOOR=0.85` for h2/h3.
3. **LF h1 wash bias** — `SN89_LF_WASH_AVOID=1` with `BTCUSD` mult (no extra volume).

### P1 (agreed — shipped)
4. LF `_bars_age_h` uses last bar timestamp; both loops fail-closed on missing/stale bars; per-pair age check before HF submit.
5. `hf.reject` → pair cooldown (`SN89_HF_REJECT_COOLDOWN_S`, default 900).
6. Wash down-weight: env-driven avoid list (+ existing `fleet_priors` BTC deny). Full public `calls[]` scrape left to `fleet_report.py` (read-only); not wired into the hot path yet.
7. Cross-key hedge block: sibling/claim open on a pair blocks **both** directions (`SN89_*_BLOCK_SIBLING_OPPOSITE`, default on).

### P2 (partial)
8. Configurable `SN89_REPO`, `SN89_DATA_ROOT`, `SN89_STATE_DIR`, `SN89_PY` in fleet launcher + wrappers (defaults preserve box paths).
9. Removed 91 tracked `__pycache__`/`*.pyc` from the index; restored `.gitignore`. Documented missing `train/*` / `run_baselines` sources in `miner_ext/README.md` (not restored — sources absent).
10. Launcher no longer sources `miner.env`; only `pm2_*_auto.sh` does. Scrub note in launcher comment.
11. Dropped `referral_onboard.py` reference from fleet launcher.
12. **Not done:** merge upstream 49 commits. Too large to mix into behaviour fixes without a dedicated sync pass; `run.sh` auto-update risk noted below.

### P3
13. `scripts/fleet_report.py` — read-only public page fetch → `$SN89_DATA_ROOT/reports/`.
14. **HF fade-spike (2026-10-16 follow-up).** `SN89_HF_DIR_MODE=fade_spike` fades ≥2σ moves over 5m on ≤30m pairs; minority relief off; bar refresh 60s + live quotes; daily cap ~20 / gap 120s. 1m-bar BT ~52% (not tick-parity). Paper / `fleet_report.py` before trusting volume.

## New flags (defaults)

| Flag | Default | Role |
|------|---------|------|
| `SN89_HF_DIVERSITY_GUARD` | `1` | Enforce floor+margin before submit |
| `SN89_HF_DIVERSITY_MARGIN` | `0.05` | Buffer above `hf_diversity` floor |
| `SN89_HF_DIVERSITY_GUARD_MIN_N` | `30` | Start buffering before hard gate at 40 |
| `SN89_HF_ALLOW_MINORITY_EDGE` | `1` | Keep weak/negative edge for repair |
| `SN89_HF_MINORITY_EDGE_TOL` | `0.02` | Floor for minority edge |
| `SN89_HF_DIVERSITY_PREFER_SHORT_HZ` | `1` | Prefer 30m while below target |
| `SN89_HF_REJECT_COOLDOWN_S` | `900` | Per-pair cooldown after `hf.reject` |
| `SN89_HF_BLOCK_SIBLING_OPPOSITE` | `1` | No fleet HF hedges |
| `SN89_STRICT_PREFER_SHORT` | `0` | LF SHORT-only bias off |
| `SN89_LF_QUALITY_FLOOR` | `0.0` | Same-dir quality score floor |
| `SN89_LF_WASH_AVOID` | `0` | Enable wash-pair down-weight |
| `SN89_LF_WASH_AVOID_PAIRS` | `BTCUSD` | CSV of pairs |
| `SN89_LF_WASH_AVOID_MULT` | `0.35` | Score multiplier |
| `SN89_LF_BLOCK_SIBLING_OPPOSITE` | `1` | No fleet LF hedges |
| `SN89_LF_BAR_MAX_AGE_H` | `3.0` | Fail-closed bar age |
| `SN89_REPO` / `SN89_DATA_ROOT` / `SN89_STATE_DIR` / `SN89_PY` | box defaults | Portable paths |

## Projected HF diversity (from local receipts, guard ON)

Using each key’s `hf_receipts_*.jsonl` and `hf.hf_diversity` at report time:

| key | n | share | floor | vs floor+0.05 | note |
|-----|---|-------|-------|---------------|------|
| h1 | 32 | 0.156 | ~0.272 | fail buffer | Guard will prefer minority / 30m or wait |
| h2 | ~33 | ~0.15 | ~0.28 | fail buffer | same |
| h3 | ~33 | ~0.18 | ~0.22 | fail buffer | same |
| h4 | ~30 | ~0.20 | ~0.22 | fail buffer | same |

After the guard is live, **projected share only rises when a minority repair clears `floor+margin`**; otherwise the loop waits (no one-sided spam into n=40). Exact post-change share depends on which minority setups appear — not a guaranteed climb to floor overnight.

## Tests

```bash
/venv/sn89/bin/python -m pytest tests/test_fleet_hf_diversity.py -q
```

(Full `pytest tests miner_ext/tests` may still fail on missing `.py` modules / env; diversity suite is the new contract.)

## Operator deploy notes

- Code-only here: **do not** auto restart pm2 from this change set.
- After deploy: restart fleet via `scripts/pm2_start_rabi_fleet.sh` when ready.
- Scrub old token in pm2 dump if present: `grep -n FEED_TOKEN ~/.pm2/dump.pm2` then edit/remove and `pm2 resurrect` / re-save after a clean start.
- `run.sh` still tracks this fork, not upstream — sync `sn89_signals/` / `neurons/` / `data/` from `DeltaCompute24/InfiniteQuant-Subnet` in a dedicated PR.

## Not verified / skipped

- Upstream 49-commit merge.
- Restoring missing `miner_ext.train.*` / `run_baselines` sources.
- End-to-end public `fleet_report` JSON schema (Next.js payload shape may need tightening).
- Live strategy backtest (P3.14) — evidence bar not met; no direction-rule change.
